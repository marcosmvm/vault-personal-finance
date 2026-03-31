-- VAULT Milestones — Progress tracking toward financial goals
-- Migration: 063_milestones

-- 1. Milestones table
create table pf_milestones (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  description text,
  target_amount numeric not null,
  current_amount numeric default 0,
  milestone_type text not null,          -- 'savings' or 'debt_payoff'
  strategy text,                         -- 'avalanche' for debt milestones
  start_date date not null,
  target_date date not null,
  completed_at timestamptz,
  status text default 'active',          -- active, completed, paused
  sort_order int default 0,
  created_at timestamptz default now()
);

-- 2. Weekly snapshots for trend tracking
create table pf_milestone_snapshots (
  id uuid primary key default gen_random_uuid(),
  milestone_id uuid references pf_milestones(id) on delete cascade,
  snapshot_date date not null,
  amount numeric not null,
  projected_completion date,
  pace text,                             -- 'ahead', 'on_track', 'behind'
  created_at timestamptz default now()
);

create index idx_milestone_snapshots_date on pf_milestone_snapshots(milestone_id, snapshot_date desc);

-- 3. Seed the 4 milestones (start_date = today, targets staggered)
insert into pf_milestones (name, description, target_amount, milestone_type, strategy, start_date, target_date, sort_order) values
  ('First $1,000 Saved',
   'Build initial savings cushion — prove you can stack $1K',
   1000.00, 'savings', null,
   now()::date, (now()::date + interval '3 months')::date, 1),

  ('Debt Free',
   'Pay off all debt using avalanche method (highest interest first)',
   0.00, 'debt_payoff', 'avalanche',
   now()::date, (now()::date + interval '12 months')::date, 2),

  ('3-Month Emergency Fund',
   '3 months of expenses ($5,250) in savings — real safety net',
   5250.00, 'savings', null,
   now()::date, (now()::date + interval '18 months')::date, 3),

  ('6-Month Emergency Fund',
   '6 months of expenses ($10,500) in savings — full financial security',
   10500.00, 'savings', null,
   now()::date, (now()::date + interval '30 months')::date, 4);


-- 4. RPC: Milestone status with live progress
create or replace function vault_milestone_status()
returns table(
  id uuid, name text, description text, target_amount numeric,
  current_amount numeric, milestone_type text, strategy text,
  start_date date, target_date date, status text, sort_order int,
  pct_complete numeric, days_remaining int, projected_completion date
)
language plpgsql stable as $$
declare
  rec record;
  live_amount numeric;
  monthly_pace numeric;
  months_remaining numeric;
begin
  for rec in select * from pf_milestones where pf_milestones.status = 'active' order by pf_milestones.sort_order loop
    -- Calculate live current amount
    if rec.milestone_type = 'savings' then
      select coalesce(sum(sb.current_amount), 0) into live_amount
      from pf_savings_buckets sb;
    elsif rec.milestone_type = 'debt_payoff' then
      select coalesce(sum(d.current_balance), 0) into live_amount
      from pf_debt d where d.current_balance > 0;
    end if;

    -- Update stored current_amount
    update pf_milestones set current_amount = live_amount where pf_milestones.id = rec.id;

    -- Calculate projected completion
    if rec.milestone_type = 'savings' then
      -- Monthly pace from savings contributions
      select coalesce(sum(sb.monthly_contribution), 0) into monthly_pace
      from pf_savings_buckets sb;
      if monthly_pace > 0 and live_amount < rec.target_amount then
        months_remaining := (rec.target_amount - live_amount) / monthly_pace;
        return query select rec.id, rec.name, rec.description, rec.target_amount,
          live_amount, rec.milestone_type, rec.strategy, rec.start_date, rec.target_date,
          rec.status, rec.sort_order,
          round((live_amount / nullif(rec.target_amount, 0)) * 100, 1),
          (rec.target_date - now()::date)::int,
          (now()::date + (months_remaining * interval '1 month'))::date;
      else
        return query select rec.id, rec.name, rec.description, rec.target_amount,
          live_amount, rec.milestone_type, rec.strategy, rec.start_date, rec.target_date,
          rec.status, rec.sort_order,
          case when rec.target_amount = 0 then 100.0
               else round((live_amount / rec.target_amount) * 100, 1) end,
          (rec.target_date - now()::date)::int,
          rec.target_date;
      end if;
    elsif rec.milestone_type = 'debt_payoff' then
      -- Monthly pace from debt payments
      select coalesce(sum(d.minimum_payment + d.monthly_extra), 0) into monthly_pace
      from pf_debt d where d.current_balance > 0;
      if monthly_pace > 0 and live_amount > 0 then
        months_remaining := live_amount / monthly_pace;
        return query select rec.id, rec.name, rec.description, rec.target_amount,
          live_amount, rec.milestone_type, rec.strategy, rec.start_date, rec.target_date,
          rec.status, rec.sort_order,
          -- For debt: progress = how much paid off vs original total
          round(((1.0 - live_amount / nullif(
            (select coalesce(sum(d2.original_balance), live_amount) from pf_debt d2), 0
          )) * 100), 1),
          (rec.target_date - now()::date)::int,
          (now()::date + (months_remaining * interval '1 month'))::date;
      else
        return query select rec.id, rec.name, rec.description, rec.target_amount,
          live_amount, rec.milestone_type, rec.strategy, rec.start_date, rec.target_date,
          rec.status, rec.sort_order,
          case when live_amount <= 0 then 100.0 else 0.0 end,
          (rec.target_date - now()::date)::int,
          rec.target_date;
      end if;
    end if;
  end loop;
end;
$$;


-- 5. RPC: Daily budget pace
create or replace function vault_daily_budget_pace()
returns table(
  total_monthly_budget numeric, spent_this_month numeric,
  days_elapsed int, days_remaining int, daily_allowance numeric
)
language sql stable as $$
  with totals as (
    select
      coalesce(sum(monthly_limit), 0) as total_budget,
      coalesce(sum(current_spent), 0) as total_spent
    from pf_budget
    where period_start <= now()::date and period_end >= now()::date
  )
  select
    t.total_budget,
    t.total_spent,
    (now()::date - date_trunc('month', now())::date)::int,
    (date_trunc('month', now())::date + interval '1 month' - interval '1 day')::date - now()::date,
    case
      when (date_trunc('month', now())::date + interval '1 month' - interval '1 day')::date - now()::date > 0
      then round((t.total_budget - t.total_spent) /
           ((date_trunc('month', now())::date + interval '1 month' - interval '1 day')::date - now()::date)::numeric, 2)
      else 0
    end
  from totals t;
$$;


-- 6. RPC: Monthly summary for a given month
create or replace function vault_monthly_summary(target_month int default extract(month from now())::int,
                                                  target_year int default extract(year from now())::int)
returns table(total_income numeric, total_expenses numeric, net_savings numeric)
language sql stable as $$
  select
    coalesce(sum(case when type = 'income' then amount else 0 end), 0),
    coalesce(sum(case when type = 'expense' then amount else 0 end), 0),
    coalesce(sum(case when type = 'income' then amount else 0 end), 0) -
    coalesce(sum(case when type = 'expense' then amount else 0 end), 0)
  from pf_transactions
  where extract(month from date) = target_month
    and extract(year from date) = target_year;
$$;


-- 7. RPC: Insert milestone snapshot (called weekly by digest)
create or replace function vault_milestone_snapshot_insert()
returns void language plpgsql as $$
declare
  ms record;
begin
  for ms in select * from vault_milestone_status() loop
    insert into pf_milestone_snapshots (milestone_id, snapshot_date, amount, projected_completion, pace)
    values (
      ms.id,
      now()::date,
      ms.current_amount,
      ms.projected_completion,
      case
        when ms.projected_completion <= ms.target_date then 'ahead'
        when ms.projected_completion <= ms.target_date + interval '14 days' then 'on_track'
        else 'behind'
      end
    );
  end loop;
end;
$$;


-- 8. RPC: Account balances for daily snapshot
create or replace function vault_account_balances()
returns table(name text, institution text, account_type text, current_balance numeric)
language sql stable as $$
  select name, institution, type, current_balance
  from pf_accounts
  order by type, current_balance desc;
$$;
