-- VAULT RPC Functions
-- Applied as Supabase migration: vault_rpc_functions
-- 16 Postgres functions callable via POST /rest/v1/rpc/

-- 1. Dedup check
create or replace function vault_dedup_check(email_id text)
returns boolean language sql stable as $$
  select exists(select 1 from pf_transactions where source_email_id = email_id);
$$;

-- 2. Upcoming bills
create or replace function vault_upcoming_bills(days_ahead int default 5)
returns table(id uuid, name text, amount numeric, due_day int, next_due date, auto_pay boolean, status text)
language sql stable as $$
  select id, name, amount, due_day, next_due, auto_pay, status
  from pf_bills where status != 'paid' and next_due <= now()::date + days_ahead
  order by next_due asc;
$$;

-- 3. Upcoming subscriptions
create or replace function vault_upcoming_subscriptions(days_ahead int default 5)
returns table(id uuid, name text, amount numeric, next_charge date, billing_cycle text, cancel_url text, priority text)
language sql stable as $$
  select id, name, amount, next_charge, billing_cycle, cancel_url, priority
  from pf_subscriptions where active = true and next_charge <= now()::date + days_ahead
  order by next_charge asc;
$$;

-- 4. Overdue bills
create or replace function vault_overdue_bills()
returns table(id uuid, name text, amount numeric, next_due date, status text)
language sql stable as $$
  select id, name, amount, next_due, status
  from pf_bills
  where status = 'overdue'
    or (due_day < extract(day from now())::int and status = 'upcoming'
        and last_paid < date_trunc('month', now())::date);
$$;

-- 5. Budget alerts
create or replace function vault_budget_alerts()
returns table(category text, monthly_limit numeric, current_spent numeric, pct_used numeric, remaining numeric)
language sql stable as $$
  select category, monthly_limit, current_spent,
    round((current_spent / nullif(monthly_limit, 0)) * 100, 1),
    monthly_limit - current_spent
  from pf_budget
  where current_spent >= (monthly_limit * alert_at_pct / 100)
    and period_start <= now()::date and period_end >= now()::date;
$$;

-- 6. Weekly cashflow
create or replace function vault_weekly_cashflow()
returns table(total_income numeric, total_expenses numeric, net_cashflow numeric)
language sql stable as $$
  select
    coalesce(sum(case when type = 'income' then amount else 0 end), 0),
    coalesce(sum(case when type = 'expense' then amount else 0 end), 0),
    coalesce(sum(case when type = 'income' then amount else 0 end), 0) -
    coalesce(sum(case when type = 'expense' then amount else 0 end), 0)
  from pf_transactions where date >= now()::date - 7;
$$;

-- 7. Income breakdown
create or replace function vault_income_breakdown()
returns table(vendor text, total numeric, count bigint)
language sql stable as $$
  select vendor, sum(amount), count(*)
  from pf_transactions where type = 'income' and date >= now()::date - 7
  group by vendor order by sum(amount) desc;
$$;

-- 8. Expense breakdown
create or replace function vault_expense_breakdown()
returns table(category text, total numeric)
language sql stable as $$
  select category, sum(amount)
  from pf_transactions where type = 'expense' and date >= now()::date - 7
  group by category order by sum(amount) desc;
$$;

-- 9. YTD tax ledger
create or replace function vault_ytd_tax_ledger(target_year int default extract(year from now())::int)
returns table(schedule_c_entity text, tax_category text, total numeric, deductible_total numeric, transaction_count bigint)
language sql stable as $$
  select schedule_c_entity, tax_category, sum(amount), sum(deductible_amount), count(*)
  from pf_transactions where tax_year = target_year
  group by schedule_c_entity, tax_category order by schedule_c_entity, sum(amount) desc;
$$;

-- 10. Needs review
create or replace function vault_needs_review()
returns table(id uuid, date date, amount numeric, vendor text, description text, tax_note text)
language sql stable as $$
  select id, date, amount, vendor, description, tax_note
  from pf_transactions where reviewed = false and tax_category = 'needs_review'
  order by date desc limit 20;
$$;

-- 11. Debt status
create or replace function vault_debt_status()
returns table(id uuid, name text, current_balance numeric, interest_rate numeric, minimum_payment numeric, monthly_extra numeric, target_payoff_date date)
language sql stable as $$
  select id, name, current_balance, interest_rate, minimum_payment, monthly_extra, target_payoff_date
  from pf_debt where current_balance > 0 order by interest_rate desc;
$$;

-- 12. Savings progress
create or replace function vault_savings_progress()
returns table(id uuid, name text, current_amount numeric, target_amount numeric, pct_complete numeric, monthly_contribution numeric)
language sql stable as $$
  select id, name, current_amount, target_amount,
    round((current_amount / nullif(target_amount, 0)) * 100, 1), monthly_contribution
  from pf_savings_buckets order by priority asc;
$$;

-- 13. Active subscriptions
create or replace function vault_active_subscriptions()
returns table(id uuid, name text, amount numeric, billing_cycle text, monthly_cost numeric, priority text, next_charge date, purpose text)
language sql stable as $$
  select id, name, amount, billing_cycle, monthly_cost, priority, next_charge, purpose
  from pf_subscriptions where active = true order by monthly_cost desc;
$$;

-- 14. Budget sync (updates + returns)
create or replace function vault_budget_sync()
returns table(category text, monthly_limit numeric, current_spent numeric, updated boolean)
language plpgsql as $$
begin
  update pf_budget set current_spent = (
    select coalesce(sum(t.amount), 0) from pf_transactions t
    where t.category = pf_budget.category
      and t.date >= pf_budget.period_start and t.date <= pf_budget.period_end
      and t.type = 'expense'
  ) where period_start <= now()::date and period_end >= now()::date;
  return query select b.category, b.monthly_limit, b.current_spent, true
    from pf_budget b where b.period_start <= now()::date and b.period_end >= now()::date;
end;
$$;

-- 15. Schedule C — Wryko
create or replace function vault_schedule_c_wryko(target_year int default extract(year from now())::int)
returns table(gross_receipts numeric, total_expenses numeric, line28_other_expenses numeric, net_profit_loss numeric)
language sql stable as $$
  select
    coalesce(sum(case when tax_category = 'business_income_wryko' then amount else 0 end), 0),
    coalesce(sum(case when schedule_c_entity = 'wryko' and type = 'expense' then deductible_amount else 0 end), 0),
    coalesce(sum(case when tax_category = 'wryko_expense' then deductible_amount else 0 end), 0),
    coalesce(sum(case when tax_category = 'business_income_wryko' then amount else 0 end), 0) -
    coalesce(sum(case when schedule_c_entity = 'wryko' and type = 'expense' then deductible_amount else 0 end), 0)
  from pf_transactions where tax_year = target_year;
$$;

-- 16. Schedule C — Coaching
create or replace function vault_schedule_c_coaching(target_year int default extract(year from now())::int)
returns table(gross_receipts numeric, total_expenses numeric, coaching_expenses numeric, net_profit_loss numeric)
language sql stable as $$
  select
    coalesce(sum(case when tax_category = 'business_income_coaching' then amount else 0 end), 0),
    coalesce(sum(case when schedule_c_entity = 'coaching' and type = 'expense' then deductible_amount else 0 end), 0),
    coalesce(sum(case when tax_category = 'coaching_expense' then deductible_amount else 0 end), 0),
    coalesce(sum(case when tax_category = 'business_income_coaching' then amount else 0 end), 0) -
    coalesce(sum(case when schedule_c_entity = 'coaching' and type = 'expense' then deductible_amount else 0 end), 0)
  from pf_transactions where tax_year = target_year;
$$;

-- 17. Budget status (all categories, not just over threshold)
create or replace function vault_budget_status()
returns table(category text, monthly_limit numeric, current_spent numeric, pct_used numeric, remaining numeric, alert_at_pct numeric)
language sql stable as $$
  select category, monthly_limit, current_spent,
    round((current_spent / nullif(monthly_limit, 0)) * 100, 1),
    monthly_limit - current_spent,
    alert_at_pct
  from pf_budget
  where period_start <= now()::date and period_end >= now()::date
  order by (current_spent / nullif(monthly_limit, 0)) desc;
$$;

-- 18. All subscriptions (active + inactive)
create or replace function vault_all_subscriptions()
returns table(id uuid, name text, vendor text, amount numeric, billing_cycle text, monthly_cost numeric, priority text, next_charge date, purpose text, active boolean, cancel_url text, category text)
language sql stable as $$
  select id, name, vendor, amount, billing_cycle, monthly_cost, priority, next_charge, purpose, active, cancel_url, category
  from pf_subscriptions order by active desc, monthly_cost desc;
$$;

-- 19. Bills overview
create or replace function vault_bills_overview()
returns table(id uuid, name text, vendor text, amount numeric, due_day int, next_due date, auto_pay boolean, status text, category text)
language sql stable as $$
  select id, name, vendor, amount, due_day, next_due, auto_pay, status, category
  from pf_bills order by next_due asc;
$$;
