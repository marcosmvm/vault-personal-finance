-- VAULT Personal Finance Schema
-- Applied as Supabase migration: vault_schema
-- Project: mrqectaahvxvwegxhydz (Wryko)

-- ============================================================
-- TABLE 1: All transactions (income + expenses)
-- ============================================================
create table pf_transactions (
  id                 uuid primary key default gen_random_uuid(),
  date               date not null,
  amount             numeric(10,2) not null,
  vendor             text,
  description        text,
  category           text,
  type               text check (type in ('income', 'expense', 'transfer', 'bill')),
  account            text,
  source_email_id    text unique,

  tax_category       text check (tax_category in (
    'business_income_wryko',
    'business_income_coaching',
    'wryko_expense',
    'coaching_expense',
    'home_office',
    'vehicle_mileage',
    'personal',
    'needs_review'
  )),
  tax_year           int default extract(year from now())::int,
  tax_note           text,
  schedule_c_entity  text check (schedule_c_entity in ('wryko', 'coaching', 'personal', 'split')),
  deductible_pct     numeric(5,2) default 100.00,
  deductible_amount  numeric(10,2),
  reviewed           boolean default false,

  created_at         timestamptz default now()
);

-- ============================================================
-- TABLE 2: Recurring bills
-- ============================================================
create table pf_bills (
  id             uuid primary key default gen_random_uuid(),
  name           text not null,
  vendor         text,
  amount         numeric(10,2),
  due_day        int,
  last_paid      date,
  next_due       date,
  status         text default 'upcoming' check (status in ('upcoming', 'paid', 'overdue', 'paused')),
  auto_pay       boolean default false,
  category       text,
  tax_category   text,
  schedule_c_entity text,
  notes          text,
  created_at     timestamptz default now()
);

-- ============================================================
-- TABLE 3: Subscriptions inventory
-- ============================================================
create table pf_subscriptions (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  vendor          text,
  amount          numeric(10,2) not null,
  billing_cycle   text check (billing_cycle in ('weekly', 'monthly', 'quarterly', 'annual')),
  next_charge     date,
  last_charged    date,
  category        text,
  purpose         text,
  tax_category    text,
  schedule_c_entity text,
  active          boolean default true,
  cancel_url      text,
  cancellation_notes text,
  account_email   text,
  priority        text check (priority in ('critical', 'high', 'medium', 'low', 'zombie')),
  monthly_cost    numeric(10,2),
  created_at      timestamptz default now()
);

-- ============================================================
-- TABLE 4: Accounts (all financial accounts)
-- ============================================================
create table pf_accounts (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  institution     text,
  type            text check (type in ('checking', 'savings', 'credit_card', 'loan', 'investment', 'cash', 'venmo', 'paypal', 'zelle')),
  current_balance numeric(12,2),
  credit_limit    numeric(12,2),
  interest_rate   numeric(6,4),
  minimum_payment numeric(10,2),
  due_date        date,
  last_updated    timestamptz,
  is_primary_checking boolean default false,
  notes           text,
  created_at      timestamptz default now()
);

-- ============================================================
-- TABLE 5: Savings buckets
-- ============================================================
create table pf_savings_buckets (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  target_amount   numeric(12,2),
  current_amount  numeric(12,2) default 0,
  monthly_contribution numeric(10,2),
  priority        int,
  account_id      uuid references pf_accounts(id),
  notes           text,
  created_at      timestamptz default now()
);

-- ============================================================
-- TABLE 6: Debt payoff tracker
-- ============================================================
create table pf_debt (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  account_id      uuid references pf_accounts(id),
  original_balance numeric(12,2),
  current_balance  numeric(12,2),
  interest_rate    numeric(6,4),
  minimum_payment  numeric(10,2),
  payoff_strategy  text check (payoff_strategy in ('avalanche', 'snowball', 'custom')),
  target_payoff_date date,
  monthly_extra    numeric(10,2) default 0,
  notes            text,
  created_at       timestamptz default now()
);

-- ============================================================
-- TABLE 7: Tax documents and forms
-- ============================================================
create table pf_tax_documents (
  id              uuid primary key default gen_random_uuid(),
  tax_year        int not null,
  form_type       text,
  entity          text,
  status          text check (status in ('draft', 'ready', 'filed', 'accepted')),
  gross_income    numeric(12,2),
  total_expenses  numeric(12,2),
  net_profit_loss numeric(12,2),
  document_json   jsonb,
  filing_date     date,
  confirmation_number text,
  notes           text,
  created_at      timestamptz default now()
);

-- ============================================================
-- TABLE 8: Budget targets
-- ============================================================
create table pf_budget (
  id              uuid primary key default gen_random_uuid(),
  category        text not null,
  monthly_limit   numeric(10,2) not null,
  current_spent   numeric(10,2) default 0,
  period_start    date,
  period_end      date,
  alert_at_pct    numeric(5,2) default 80.00,
  notes           text
);

-- ============================================================
-- TABLE 9: Weekly digest log
-- ============================================================
create table pf_digest_log (
  id              uuid primary key default gen_random_uuid(),
  week_start      date not null,
  week_end        date not null,
  digest_text     text,
  total_income    numeric(12,2),
  total_expenses  numeric(12,2),
  net_cashflow    numeric(12,2),
  ytd_wryko_expenses numeric(12,2),
  ytd_coaching_income numeric(12,2),
  needs_review_count int,
  action_item     text,
  sent_at         timestamptz,
  created_at      timestamptz default now()
);

-- ============================================================
-- INDEXES
-- ============================================================
create index idx_transactions_date on pf_transactions(date desc);
create index idx_transactions_tax on pf_transactions(tax_year, tax_category);
create index idx_transactions_review on pf_transactions(reviewed) where reviewed = false;
create index idx_subscriptions_active on pf_subscriptions(active) where active = true;
create index idx_debt_balance on pf_debt(current_balance desc);
