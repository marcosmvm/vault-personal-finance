-- VAULT Seed Data — Real financial data for Marcos Matthews
-- Migration: 064_seed_real_data
-- This populates pf_bills, pf_budget, pf_debt, pf_savings_buckets
-- with actual data so dashboard tabs display real information.
-- pf_accounts should already be populated; pf_subscriptions is auto-populated by intake agent.

-- ============================================================
-- RECURRING BILLS
-- ============================================================
insert into pf_bills (name, vendor, amount, due_day, next_due, status, auto_pay, category, tax_category, schedule_c_entity) values
  ('Toyota Payment',     'Toyota Financial',  350.00, 15, (date_trunc('month', now()) + interval '1 month' + interval '14 days')::date, 'upcoming', true,  'transport', 'personal',       'personal'),
  ('Verizon Wireless',   'Verizon',           85.00,  22, (date_trunc('month', now()) + interval '1 month' + interval '21 days')::date, 'upcoming', true,  'utilities', 'personal',       'personal'),
  ('AAA Insurance',      'AAA',               145.00, 10, (date_trunc('month', now()) + interval '1 month' + interval '9 days')::date,  'upcoming', true,  'transport', 'personal',       'personal'),
  ('Rent',               'Landlord',          975.00, 1,  (date_trunc('month', now()) + interval '1 month')::date,                      'upcoming', false, 'housing',   'home_office',    'split'),
  ('Gas & Electric',     'SoCal Edison',      120.00, 5,  (date_trunc('month', now()) + interval '1 month' + interval '4 days')::date,  'upcoming', true,  'utilities', 'home_office',    'split'),
  ('Internet',           'Spectrum',          75.00,  12, (date_trunc('month', now()) + interval '1 month' + interval '11 days')::date, 'upcoming', true,  'utilities', 'home_office',    'split')
on conflict do nothing;

-- ============================================================
-- MONTHLY BUDGET LIMITS
-- Budget periods auto-set to current month
-- ============================================================
insert into pf_budget (category, monthly_limit, current_spent, period_start, period_end, alert_at_pct) values
  ('food',           400.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 80),
  ('transport',      250.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 80),
  ('tools',          500.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 90),
  ('coaching',       150.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 80),
  ('entertainment',  100.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 80),
  ('subscriptions',  350.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 90),
  ('housing',        1100.00,0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 95),
  ('utilities',      300.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 85),
  ('debt',           500.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 95),
  ('other',          200.00, 0, date_trunc('month', now())::date, (date_trunc('month', now()) + interval '1 month' - interval '1 day')::date, 80)
on conflict do nothing;

-- ============================================================
-- SAVINGS GOALS
-- ============================================================
insert into pf_savings_buckets (name, target_amount, current_amount, monthly_contribution, priority, notes) values
  ('Emergency Fund',        5250.00,  0, 200.00, 1, '3 months of $1,750 expenses'),
  ('Q2 Tax Reserve',        550.00,   0, 275.00, 2, 'Quarterly estimated tax payment'),
  ('Wryko Runway',          3000.00,  0, 150.00, 3, '2 months of infrastructure costs'),
  ('Coaching Equipment',    500.00,   0, 50.00,  4, 'Annual gear refresh — cones, pinnies, balls'),
  ('Personal Goals',        2000.00,  0, 100.00, 5, 'Travel, personal purchases')
on conflict do nothing;

-- ============================================================
-- TRANSACTION-DERIVED RPCs
-- These pull real spending data from pf_transactions
-- ============================================================

-- Budget spending computed from actual transactions (current month)
-- This syncs pf_budget.current_spent from real transaction data
create or replace function vault_budget_sync_full()
returns table(category text, monthly_limit numeric, current_spent numeric, pct_used numeric, remaining numeric, alert_at_pct numeric)
language plpgsql as $$
begin
  -- Update current_spent for each budget category from real transactions
  update pf_budget b set current_spent = (
    select coalesce(sum(t.amount), 0)
    from pf_transactions t
    where t.category = b.category
      and t.type in ('expense', 'bill')
      and t.date >= b.period_start
      and t.date <= b.period_end
  ) where b.period_start <= now()::date and b.period_end >= now()::date;

  -- Return updated budget status
  return query
    select b.category, b.monthly_limit, b.current_spent,
      round((b.current_spent / nullif(b.monthly_limit, 0)) * 100, 1),
      b.monthly_limit - b.current_spent,
      b.alert_at_pct
    from pf_budget b
    where b.period_start <= now()::date and b.period_end >= now()::date
    order by (b.current_spent / nullif(b.monthly_limit, 0)) desc;
end;
$$;

-- Spending by category from transactions (works even without pf_budget rows)
create or replace function vault_spending_by_category(target_year int default extract(year from now())::int)
returns table(category text, total_spent numeric, transaction_count bigint, avg_per_txn numeric)
language sql stable as $$
  select
    coalesce(category, 'other'),
    sum(amount),
    count(*),
    round(avg(amount), 2)
  from pf_transactions
  where type in ('expense', 'bill')
    and tax_year = target_year
  group by category
  order by sum(amount) desc;
$$;

-- Recurring charges detected from transaction patterns
create or replace function vault_recurring_charges()
returns table(vendor text, avg_amount numeric, charge_count bigint, last_charged date, frequency text, schedule_c_entity text)
language sql stable as $$
  select
    vendor,
    round(avg(amount), 2),
    count(*),
    max(date),
    case
      when count(*) >= 4 and (max(date) - min(date)) / nullif(count(*) - 1, 0) between 25 and 35 then 'monthly'
      when count(*) >= 2 and (max(date) - min(date)) / nullif(count(*) - 1, 0) between 80 and 100 then 'quarterly'
      when count(*) >= 2 and (max(date) - min(date)) / nullif(count(*) - 1, 0) between 350 and 380 then 'annual'
      when count(*) >= 8 and (max(date) - min(date)) / nullif(count(*) - 1, 0) between 5 and 9 then 'weekly'
      else 'irregular'
    end,
    mode() within group (order by schedule_c_entity)
  from pf_transactions
  where type in ('expense', 'bill')
    and date >= now()::date - 365
    and vendor is not null
    and vendor != ''
  group by vendor
  having count(*) >= 2
  order by count(*) desc, avg(amount) desc;
$$;

-- Weekly spending trend (last 12 weeks)
create or replace function vault_weekly_spending_trend()
returns table(week_start date, total_income numeric, total_expenses numeric, net numeric)
language sql stable as $$
  select
    date_trunc('week', date)::date,
    coalesce(sum(case when type = 'income' then amount else 0 end), 0),
    coalesce(sum(case when type in ('expense', 'bill') then amount else 0 end), 0),
    coalesce(sum(case when type = 'income' then amount else 0 end), 0) -
    coalesce(sum(case when type in ('expense', 'bill') then amount else 0 end), 0)
  from pf_transactions
  where date >= now()::date - 84
  group by date_trunc('week', date)
  order by date_trunc('week', date) desc;
$$;

-- Income sources YTD
create or replace function vault_income_sources(target_year int default extract(year from now())::int)
returns table(vendor text, entity text, total numeric, transaction_count bigint, last_received date)
language sql stable as $$
  select
    vendor,
    schedule_c_entity,
    sum(amount),
    count(*),
    max(date)
  from pf_transactions
  where type = 'income'
    and tax_year = target_year
  group by vendor, schedule_c_entity
  order by sum(amount) desc;
$$;
