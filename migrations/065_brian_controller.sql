-- VAULT "Brian" Controller — Autonomous Financial Management
-- Migration: 065_brian_controller
-- Adds allocation engine, payment instructions, cash flow projections,
-- debt tracking enhancements, and all supporting RPC functions.

-- ============================================================
-- TABLE: Allocation plans (zero-based budgeting on income)
-- ============================================================
create table pf_allocation_plans (
  id                     uuid primary key default gen_random_uuid(),
  trigger_type           text not null check (trigger_type in ('income_detected', 'manual', 'weekly_rebalance')),
  trigger_amount         numeric(12,2),
  trigger_transaction_id uuid references pf_transactions(id),
  plan_json              jsonb not null,
  status                 text default 'pending' check (status in ('pending', 'acknowledged', 'executed', 'expired')),
  created_at             timestamptz default now(),
  acknowledged_at        timestamptz
);

create index idx_allocation_plans_status on pf_allocation_plans(status) where status = 'pending';

-- ============================================================
-- TABLE: Payment instructions (derived from allocation plans)
-- ============================================================
create table pf_payment_instructions (
  id                  uuid primary key default gen_random_uuid(),
  allocation_plan_id  uuid references pf_allocation_plans(id) on delete cascade,
  instruction_type    text not null check (instruction_type in (
    'bill_payment', 'debt_payment', 'savings_transfer', 'discretionary'
  )),
  payee               text not null,
  amount              numeric(12,2) not null,
  from_account        text,
  due_date            date,
  priority            int not null check (priority between 1 and 5),
  -- 1=bills, 2=debt minimums, 3=debt extra (avalanche), 4=savings, 5=discretionary
  status              text default 'pending' check (status in ('pending', 'completed', 'skipped')),
  completed_at        timestamptz,
  notes               text,
  created_at          timestamptz default now()
);

create index idx_payment_instructions_status on pf_payment_instructions(status, due_date)
  where status = 'pending';
create index idx_payment_instructions_plan on pf_payment_instructions(allocation_plan_id);

-- ============================================================
-- TABLE: Cash flow projections (90-day lookahead)
-- ============================================================
create table pf_cashflow_projections (
  id                  uuid primary key default gen_random_uuid(),
  projection_date     date not null,
  account_name        text not null,
  projected_balance   numeric(12,2) not null,
  inflow              numeric(12,2) default 0,
  outflow             numeric(12,2) default 0,
  confidence          text default 'estimated' check (confidence in ('confirmed', 'expected', 'estimated')),
  source_description  text,
  batch_id            uuid not null,
  created_at          timestamptz default now()
);

create index idx_cashflow_batch on pf_cashflow_projections(batch_id);
create index idx_cashflow_date on pf_cashflow_projections(projection_date, account_name);

-- ============================================================
-- ALTER: Add tracking columns to pf_debt
-- ============================================================
alter table pf_debt add column if not exists last_payment_date date;
alter table pf_debt add column if not exists total_interest_paid numeric(12,2) default 0;
alter table pf_debt add column if not exists total_paid numeric(12,2) default 0;

-- ============================================================
-- RPC: Unallocated income (income without an allocation plan)
-- ============================================================
create or replace function vault_unallocated_income()
returns table(id uuid, date date, amount numeric, vendor text, description text, account text)
language sql stable as $$
  select t.id, t.date, t.amount, t.vendor, t.description, t.account
  from pf_transactions t
  left join pf_allocation_plans ap on ap.trigger_transaction_id = t.id
  where t.type = 'income'
    and t.date >= now()::date - 7
    and ap.id is null
  order by t.date desc;
$$;

-- ============================================================
-- RPC: Upcoming obligations (bills + debt minimums + subscriptions)
-- ============================================================
create or replace function vault_upcoming_obligations(days_ahead int default 30)
returns table(
  source text, name text, amount numeric, due_date date,
  from_account text, is_auto_pay boolean, obligation_type text
)
language sql stable as $$
  select * from (
    select 'bill'::text as source, b.name, b.amount, b.next_due as due_date, null::text as from_account, b.auto_pay as is_auto_pay, 'bill'::text as obligation_type
    from pf_bills b
    where b.status != 'paid' and b.next_due <= now()::date + days_ahead
    union all
    select 'debt'::text, d.name || ' (minimum)', d.minimum_payment,
      coalesce(a.due_date, (date_trunc('month', now()) + interval '1 month')::date),
      a.name, false, 'debt_minimum'::text
    from pf_debt d
    left join pf_accounts a on a.id = d.account_id
    where d.current_balance > 0
    union all
    select 'subscription'::text, s.name, s.amount, s.next_charge, null::text, true, 'subscription'::text
    from pf_subscriptions s
    where s.active = true and s.next_charge <= now()::date + days_ahead
  ) sub
  order by sub.due_date asc;
$$;

-- ============================================================
-- RPC: Available for allocation (checking balance - 30-day obligations)
-- ============================================================
create or replace function vault_available_for_allocation()
returns table(
  checking_balance numeric, total_obligations numeric,
  allocatable_surplus numeric, primary_account text
)
language sql stable as $$
  with checking as (
    select name, current_balance
    from pf_accounts where is_primary_checking = true limit 1
  ),
  obligations as (
    select coalesce(sum(amount), 0) as total
    from vault_upcoming_obligations(30)
  )
  select c.current_balance, o.total, c.current_balance - o.total, c.name
  from checking c, obligations o;
$$;

-- ============================================================
-- RPC: Debt avalanche order with months-to-payoff calculation
-- ============================================================
create or replace function vault_debt_avalanche_order()
returns table(
  id uuid, name text, current_balance numeric, interest_rate numeric,
  minimum_payment numeric, monthly_extra numeric, monthly_interest numeric,
  months_to_payoff numeric, projected_payoff_date date, total_interest_remaining numeric
)
language sql stable as $$
  select
    d.id, d.name, d.current_balance, d.interest_rate,
    d.minimum_payment, d.monthly_extra,
    -- Monthly interest
    round(d.current_balance * d.interest_rate / 100.0 / 12.0, 2),
    -- Months to payoff (simplified: balance / (payment - monthly interest))
    case
      when (d.minimum_payment + d.monthly_extra) > (d.current_balance * d.interest_rate / 100.0 / 12.0)
      then round(
        d.current_balance / ((d.minimum_payment + d.monthly_extra) - (d.current_balance * d.interest_rate / 100.0 / 12.0)),
        1
      )
      else 999.0  -- Cannot pay off at current rate
    end,
    -- Projected payoff date
    case
      when (d.minimum_payment + d.monthly_extra) > (d.current_balance * d.interest_rate / 100.0 / 12.0)
      then (now()::date + (
        d.current_balance / ((d.minimum_payment + d.monthly_extra) - (d.current_balance * d.interest_rate / 100.0 / 12.0))
        * interval '1 month'
      ))::date
      else null
    end,
    -- Total interest remaining (simplified estimate)
    case
      when (d.minimum_payment + d.monthly_extra) > (d.current_balance * d.interest_rate / 100.0 / 12.0)
      then round(
        (d.current_balance * d.interest_rate / 100.0 / 12.0) *
        (d.current_balance / ((d.minimum_payment + d.monthly_extra) - (d.current_balance * d.interest_rate / 100.0 / 12.0))) / 2.0,
        2
      )
      else null
    end
  from pf_debt d
  where d.current_balance > 0
  order by d.interest_rate desc;
$$;

-- ============================================================
-- RPC: Bill payment schedule with running balance projection
-- ============================================================
create or replace function vault_bill_payment_schedule(days_ahead int default 14)
returns table(
  name text, amount numeric, due_date date, from_account text,
  is_auto_pay boolean, obligation_type text, running_balance numeric
)
language sql stable as $$
  with checking as (
    select current_balance from pf_accounts where is_primary_checking = true limit 1
  ),
  obligations as (
    select o.name, o.amount, o.due_date, o.from_account, o.is_auto_pay, o.obligation_type,
      row_number() over (order by o.due_date asc, o.amount desc) as rn
    from vault_upcoming_obligations(days_ahead) o
  )
  select o.name, o.amount, o.due_date, o.from_account, o.is_auto_pay, o.obligation_type,
    c.current_balance - sum(o.amount) over (order by o.due_date asc, o.amount desc rows unbounded preceding)
  from obligations o, checking c
  order by o.due_date asc, o.amount desc;
$$;

-- ============================================================
-- RPC: Debt payoff projection with cascade (avalanche strategy)
-- ============================================================
create or replace function vault_debt_payoff_projection(extra_monthly numeric default 0)
returns table(
  name text, current_balance numeric, interest_rate numeric,
  min_payment numeric, extra_payment numeric,
  months_to_payoff numeric, payoff_date date, total_interest numeric
)
language plpgsql stable as $$
declare
  debt record;
  freed numeric := extra_monthly;
  bal numeric;
  monthly_rate numeric;
  months numeric;
  interest numeric;
begin
  for debt in
    select d.name, d.current_balance, d.interest_rate, d.minimum_payment, d.monthly_extra
    from pf_debt d where d.current_balance > 0
    order by d.interest_rate desc
  loop
    bal := debt.current_balance;
    monthly_rate := debt.interest_rate / 100.0 / 12.0;

    -- For the first (highest rate) debt, add all freed payments
    if freed > 0 then
      -- Calculate with extra
      if (debt.minimum_payment + debt.monthly_extra + freed) > (bal * monthly_rate) then
        months := 0;
        interest := 0;
        while bal > 0 loop
          interest := interest + (bal * monthly_rate);
          bal := bal + (bal * monthly_rate) - (debt.minimum_payment + debt.monthly_extra + freed);
          months := months + 1;
          if months > 600 then exit; end if;
        end loop;
      else
        months := 999;
        interest := null;
      end if;

      return query select debt.name, debt.current_balance, debt.interest_rate,
        debt.minimum_payment, debt.monthly_extra + freed,
        months, (now()::date + (months * interval '1 month'))::date, round(interest, 2);

      -- After this debt is paid off, its minimum + extra is freed for the next debt
      freed := freed + debt.minimum_payment + debt.monthly_extra;
    else
      -- Calculate without extra
      if (debt.minimum_payment + debt.monthly_extra) > (bal * monthly_rate) then
        months := 0;
        interest := 0;
        while bal > 0 loop
          interest := interest + (bal * monthly_rate);
          bal := bal + (bal * monthly_rate) - (debt.minimum_payment + debt.monthly_extra);
          months := months + 1;
          if months > 600 then exit; end if;
        end loop;
      else
        months := 999;
        interest := null;
      end if;

      return query select debt.name, debt.current_balance, debt.interest_rate,
        debt.minimum_payment, debt.monthly_extra,
        months, (now()::date + (months * interval '1 month'))::date, round(interest, 2);

      freed := debt.minimum_payment + debt.monthly_extra;
    end if;
  end loop;
end;
$$;

-- ============================================================
-- RPC: Income pattern analysis
-- ============================================================
create or replace function vault_income_pattern(months_back int default 3)
returns table(
  source text, avg_monthly numeric, total numeric,
  month_count bigint, trend text
)
language sql stable as $$
  with monthly as (
    select
      coalesce(schedule_c_entity, 'other') as source,
      date_trunc('month', date) as month,
      sum(amount) as monthly_total
    from pf_transactions
    where type = 'income' and date >= now()::date - (months_back * 30)
    group by 1, 2
  ),
  stats as (
    select source,
      round(avg(monthly_total), 2) as avg_monthly,
      sum(monthly_total) as total,
      count(distinct month) as month_count,
      -- Simple trend: compare last month vs average
      case
        when count(distinct month) < 2 then 'insufficient_data'
        when max(case when month = date_trunc('month', now() - interval '1 month') then monthly_total end) >
             avg(monthly_total) * 1.1 then 'increasing'
        when max(case when month = date_trunc('month', now() - interval '1 month') then monthly_total end) <
             avg(monthly_total) * 0.9 then 'decreasing'
        else 'stable'
      end as trend
    from monthly group by source
  )
  select * from stats order by avg_monthly desc;
$$;

-- ============================================================
-- RPC: Balance sheet snapshot
-- ============================================================
create or replace function vault_balance_sheet()
returns table(
  total_assets numeric, total_liabilities numeric, net_worth numeric,
  checking_total numeric, savings_total numeric, credit_card_total numeric,
  loan_total numeric, other_assets numeric
)
language sql stable as $$
  with accounts as (
    select
      coalesce(sum(case when type in ('checking', 'savings', 'investment', 'cash', 'venmo', 'paypal', 'zelle')
                        and current_balance > 0 then current_balance else 0 end), 0) as assets,
      coalesce(sum(case when type in ('credit_card', 'loan') then abs(current_balance) else 0 end), 0) +
      coalesce((select sum(current_balance) from pf_debt where current_balance > 0), 0) as liabilities,
      coalesce(sum(case when type = 'checking' then current_balance else 0 end), 0) as checking,
      coalesce(sum(case when type = 'savings' then current_balance else 0 end), 0) as savings,
      coalesce(sum(case when type = 'credit_card' then abs(current_balance) else 0 end), 0) as cc,
      coalesce(sum(case when type = 'loan' then abs(current_balance) else 0 end), 0) as loans,
      coalesce(sum(case when type in ('investment', 'cash', 'venmo', 'paypal', 'zelle')
                        and current_balance > 0 then current_balance else 0 end), 0) as other
    from pf_accounts
  )
  select a.assets, a.liabilities, a.assets - a.liabilities,
    a.checking, a.savings, a.cc, a.loans, a.other
  from accounts a;
$$;

-- ============================================================
-- RPC: Monthly P&L by entity
-- ============================================================
create or replace function vault_monthly_pl(
  target_month int default extract(month from now())::int,
  target_year int default extract(year from now())::int
)
returns table(
  entity text, income numeric, expenses numeric, net numeric, category text, category_total numeric
)
language sql stable as $$
  -- Summary by entity
  select
    coalesce(schedule_c_entity, 'personal') as entity,
    coalesce(sum(case when type = 'income' then amount end), 0) as income,
    coalesce(sum(case when type = 'expense' then amount end), 0) as expenses,
    coalesce(sum(case when type = 'income' then amount end), 0) -
    coalesce(sum(case when type = 'expense' then amount end), 0) as net,
    category,
    sum(amount) as category_total
  from pf_transactions
  where extract(month from date) = target_month
    and extract(year from date) = target_year
  group by grouping sets (
    (schedule_c_entity),
    (schedule_c_entity, category)
  )
  order by entity, category_total desc;
$$;

-- ============================================================
-- RPC: Net worth trend (monthly snapshots from accounts)
-- ============================================================
create or replace function vault_net_worth_trend(months_back int default 6)
returns table(month date, net_worth numeric)
language sql stable as $$
  with savings_monthly as (
    select distinct on (date_trunc('month', ms.snapshot_date))
      date_trunc('month', ms.snapshot_date)::date as month,
      ms.amount
    from pf_milestone_snapshots ms
    join pf_milestones m on m.id = ms.milestone_id
    where m.milestone_type = 'savings' and m.sort_order = 3
      and ms.snapshot_date >= now()::date - (months_back * 30)
    order by date_trunc('month', ms.snapshot_date), ms.snapshot_date desc
  ),
  debt_monthly as (
    select distinct on (date_trunc('month', ms.snapshot_date))
      date_trunc('month', ms.snapshot_date)::date as month,
      ms.amount
    from pf_milestone_snapshots ms
    join pf_milestones m on m.id = ms.milestone_id
    where m.milestone_type = 'debt_payoff'
      and ms.snapshot_date >= now()::date - (months_back * 30)
    order by date_trunc('month', ms.snapshot_date), ms.snapshot_date desc
  )
  select coalesce(s.month, d.month) as month,
    coalesce(s.amount, 0) - coalesce(d.amount, 0) as net_worth
  from savings_monthly s
  full outer join debt_monthly d on s.month = d.month
  order by 1 asc;
$$;

-- ============================================================
-- RPC: Budget rotation (create next month's budget periods)
-- ============================================================
create or replace function vault_budget_rotate()
returns int language plpgsql as $$
declare
  next_start date := date_trunc('month', now() + interval '1 month')::date;
  next_end date := (date_trunc('month', now() + interval '2 months') - interval '1 day')::date;
  created int := 0;
begin
  -- Only create if next month doesn't already have budget entries
  if not exists (select 1 from pf_budget where period_start = next_start) then
    insert into pf_budget (category, monthly_limit, current_spent, period_start, period_end, alert_at_pct, notes)
    select category, monthly_limit, 0, next_start, next_end, alert_at_pct, notes
    from pf_budget
    where period_start = date_trunc('month', now())::date;

    get diagnostics created = row_count;
  end if;
  return created;
end;
$$;

-- ============================================================
-- RPC: Affordability check for decision engine
-- ============================================================
create or replace function vault_affordability_check(proposed_amount numeric)
returns table(
  checking_balance numeric, obligations_30d numeric,
  balance_after_obligations numeric, balance_after_purchase numeric,
  can_afford boolean, safety_margin numeric
)
language sql stable as $$
  with avail as (
    select * from vault_available_for_allocation()
  )
  select
    a.checking_balance,
    a.total_obligations,
    a.allocatable_surplus,
    a.allocatable_surplus - proposed_amount,
    (a.allocatable_surplus - proposed_amount) >= 500,  -- $500 safety floor
    a.allocatable_surplus - proposed_amount - 500
  from avail a;
$$;
