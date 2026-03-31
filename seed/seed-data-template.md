# VAULT — Seed Data Template

Fill in your real financial data below. Once complete, VAULT will generate SQL and load it into Supabase.

---

## 1. Financial Accounts

| Name | Institution | Type | Current Balance | Credit Limit | Interest Rate (APR) | Minimum Payment | Due Date | Primary Checking? | Notes |
|------|-------------|------|-----------------|--------------|---------------------|-----------------|----------|-------------------|-------|
| Example Checking | Chase | checking | 2500.00 | | | | | yes | Main account |
| Example CC | Chase | credit_card | -2840.00 | 5000.00 | 22.40 | 85.00 | 15th | no | Sapphire |
| Venmo | Venmo | venmo | 150.00 | | | | | no | |
| | | | | | | | | | |
| | | | | | | | | | |
| | | | | | | | | | |

**Types:** checking, savings, credit_card, loan, investment, cash, venmo, paypal, zelle

---

## 2. Recurring Bills

| Name | Vendor | Amount | Due Day (of month) | Auto-Pay? | Category | Tax Category | Schedule C Entity | Notes |
|------|--------|--------|---------------------|-----------|----------|--------------|-------------------|-------|
| Example: Rent | Landlord | 1800.00 | 1 | no | housing | home_office | split | |
| Example: n8n | n8n.io | 168.00 | 15 | yes | tools | wryko_expense | wryko | Cloud plan |
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |

---

## 3. Active Subscriptions

| Name | Vendor | Amount | Billing Cycle | Purpose | Tax Category | Schedule C Entity | Priority | Cancel URL | Account Email |
|------|--------|--------|---------------|---------|--------------|-------------------|----------|------------|---------------|
| Example: Anthropic | Anthropic | 84.00 | monthly | AI API for Wryko | wryko_expense | wryko | critical | | marcos@wryko.com |
| Example: Spotify | Spotify | 11.00 | monthly | Music | personal | personal | low | spotify.com/account | marcosmvm1515@gmail.com |
| | | | | | | | | | |
| | | | | | | | | | |
| | | | | | | | | | |

**Billing Cycles:** weekly, monthly, quarterly, annual
**Priorities:** critical, high, medium, low, zombie

---

## 4. Current Debts

| Name | Original Balance | Current Balance | Interest Rate (APR) | Minimum Payment | Strategy | Target Payoff Date | Monthly Extra | Notes |
|------|-----------------|-----------------|---------------------|-----------------|----------|-------------------|---------------|-------|
| Example: Chase Sapphire | 5000.00 | 2840.00 | 22.40 | 85.00 | avalanche | | 100.00 | |
| Example: Student Loan | 15000.00 | 8100.00 | 5.10 | 150.00 | avalanche | | 0.00 | |
| | | | | | | | | |
| | | | | | | | | |

**Strategies:** avalanche (highest rate first), snowball (lowest balance first), custom

---

## 5. Savings Goals

| Bucket Name | Target Amount | Current Amount | Monthly Contribution | Priority (1=first) | Notes |
|-------------|---------------|----------------|---------------------|---------------------|-------|
| Emergency Fund | 5000.00 | 0.00 | 200.00 | 1 | 3 months expenses |
| Q2 Tax Reserve | 550.00 | 0.00 | 275.00 | 2 | Quarterly estimated taxes |
| Wryko Runway | 3000.00 | 0.00 | 150.00 | 3 | 2 months infra costs |
| Coaching Equipment | 500.00 | 0.00 | 50.00 | 4 | Annual gear refresh |
| Personal Goals | 2000.00 | 0.00 | 100.00 | 5 | Travel, personal |
| | | | | | |

---

## 6. Monthly Budget Limits

| Category | Monthly Limit | Alert At (%) | Notes |
|----------|---------------|--------------|-------|
| food | 400.00 | 80 | Groceries + dining |
| transport | 200.00 | 80 | Gas, rideshare |
| tools | 500.00 | 90 | SaaS/infra (Wryko) |
| coaching | 150.00 | 80 | Equipment, fields |
| entertainment | 100.00 | 80 | |
| subscriptions | 300.00 | 90 | All recurring charges |
| other | 200.00 | 80 | Miscellaneous |
| | | | |

---

## Instructions

1. Replace all example rows with your real data
2. Add as many rows as needed — delete the blank template rows
3. Delete example rows you don't need
4. Save this file and tell VAULT to process it
5. VAULT will generate the SQL and load it into Supabase
