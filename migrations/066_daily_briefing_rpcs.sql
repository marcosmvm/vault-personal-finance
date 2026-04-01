-- 066: Daily briefing RPCs — transactions list + weekly entity summary
-- Used by watchdog.py for the redesigned daily email

-- 1. Today's transactions list
CREATE OR REPLACE FUNCTION vault_daily_transactions(
    target_date date DEFAULT now()::date
)
RETURNS TABLE (
    vendor      text,
    amount      numeric,
    type        text,
    category    text,
    description text,
    schedule_c_entity text,
    tax_category      text,
    deductible_amount numeric
)
LANGUAGE sql STABLE
AS $$
    SELECT
        vendor,
        amount,
        type,
        category,
        description,
        schedule_c_entity,
        tax_category,
        deductible_amount
    FROM pf_transactions
    WHERE date = target_date
    ORDER BY amount DESC;
$$;

-- 2. Week-to-date summary grouped by entity
--    Week starts Monday. Returns one row per entity with income, expenses,
--    tax-relevant amounts, and writeoff (deductible) totals.
CREATE OR REPLACE FUNCTION vault_weekly_entity_summary(
    target_date date DEFAULT now()::date
)
RETURNS TABLE (
    entity     text,
    income     numeric,
    expenses   numeric,
    taxes      numeric,
    writeoffs  numeric
)
LANGUAGE sql STABLE
AS $$
    WITH week_bounds AS (
        SELECT
            target_date - ((EXTRACT(ISODOW FROM target_date)::int - 1)) AS week_start,
            target_date AS week_end
    )
    SELECT
        COALESCE(t.schedule_c_entity, 'personal') AS entity,
        COALESCE(SUM(t.amount) FILTER (WHERE t.type = 'income'), 0) AS income,
        COALESCE(SUM(t.amount) FILTER (WHERE t.type IN ('expense', 'bill')), 0) AS expenses,
        COALESCE(SUM(t.amount) FILTER (
            WHERE t.tax_category LIKE 'business_%'
        ), 0) AS taxes,
        COALESCE(SUM(t.deductible_amount), 0) AS writeoffs
    FROM pf_transactions t, week_bounds w
    WHERE t.date BETWEEN w.week_start AND w.week_end
    GROUP BY COALESCE(t.schedule_c_entity, 'personal');
$$;
