"""Fernway: a larger, made-up Series A dev-tools company for the demo.

~25 people, ~45 card vendors, weekly Stripe payouts. Same 12-month Runway
layout as Slope, with roughly four times the transactions.
"""

from __future__ import annotations

from synth.company import OtherItem, Profile

_EMPLOYEES = (
    ("Maya Lindqvist", 235000, 0, 11), ("Omar Haddad", 220000, 0, 11),
    ("Priya Raman", 210000, 0, 11), ("Tomas Beaulieu", 200000, 0, 11),
    ("Nadia Petrov", 185000, 0, 11), ("Lucas Ferreira", 180000, 0, 11),
    ("Hana Sato", 175000, 0, 11), ("Gabe Thornton", 170000, 0, 11),
    ("Imani Brooks", 165000, 0, 11), ("Jonas Weber", 160000, 0, 11),
    ("Keiko Tanaka", 155000, 0, 11), ("Leo Marchetti", 150000, 0, 11),
    ("Mei Zhou", 150000, 0, 11), ("Noah Abrams", 145000, 0, 7),
    ("Olivia Reyes", 140000, 0, 11), ("Pavel Novak", 140000, 0, 11),
    ("Quinn Murphy", 135000, 0, 11), ("Rosa Delgado", 130000, 0, 11),
    ("Sam Okonkwo", 130000, 2, 11), ("Tara Singh", 125000, 2, 11),
    ("Uma Kapoor", 145000, 4, 11), ("Victor Hale", 140000, 4, 11),
    ("Wen Li", 135000, 6, 11), ("Xavi Costa", 130000, 8, 11),
    ("Yara Aziz", 125000, 9, 11),
)

_SAAS_MONTHLY = (
    ("GitLab", 29 * 2900), ("Notion", 1600 * 25), ("Slack", 8750 * 4),
    ("Linear", 800 * 25), ("Zoom", 1599 * 12), ("Google Workspace", 1200 * 27),
    ("Datadog", 118000), ("Sentry", 26000), ("Vercel", 42000),
    ("HubSpot", 160000), ("Intercom", 74000), ("Segment", 52000),
    ("OpenAI", 310000), ("1Password", 12500), ("Okta", 33000),
    ("PagerDuty", 41000), ("LaunchDarkly", 36000), ("Snowflake", 245000),
    ("Cloudflare", 20000), ("Twilio", 58000), ("SendGrid", 15000),
    ("Algolia", 29000), ("Amplitude", 69000), ("Gong", 120000),
    ("Salesforce", 175000), ("Asana", 22000), ("Miro", 12000),
    ("Figma", 4500 * 8), ("Webflow", 4900), ("Typeform", 7000),
    ("Calendly", 9600), ("DocuSign", 12000), ("Gusto", 30000),
    ("Brex Travel", 5000), ("Lattice", 41000), ("Greenhouse", 38000),
    ("Postman", 22000), ("CircleCI", 31500), ("Honeycomb", 27500),
    ("Mux", 18000), ("Pinecone", 24000), ("Supabase", 12500),
    ("Retool", 22500), ("Airtable", 16000),
)

_SAAS_ANNUAL = (
    ("Loom", 19200, 1), ("Canva", 36000, 2), ("Mixpanel", 216000, 4),
    ("Zapier", 119976, 5), ("Ahrefs", 99900, 7), ("Grammarly", 24000, 9),
)

FERNWAY = Profile(
    name="Fernway",
    starting_cash_cents=680_000_000,  # $6.8M
    employees=_EMPLOYEES,
    saas_monthly=_SAAS_MONTHLY,
    saas_annual=_SAAS_ANNUAL,
    late_vendor="Clickhouse Cloud",
    late_first_month=6,
    late_cents=88000,
    contractors=("Upwork", "Toptal", "Deel Contractors", "Catalant",
                 "Fiverr Pro", "Gun.io", "A.Team", "Braintrust"),
    contractor_prob=0.7,
    contractor_range=(600_000, 2_400_000),
    aws_base=64000,
    aws_growth=4200,
    other=(
        OtherItem("WeWork Supplies", "Office", 8000, 42000, "", day=10),
        OtherItem("Staples", "Office", 4000, 26000, "", n=3),
        OtherItem("Amazon Business", "Office", 3000, 38000, "", n=4),
        OtherItem("Google Ads", "Marketing", 2_200_000, 4_200_000, "", day=14),
        OtherItem("LinkedIn Ads", "Marketing", 800_000, 1_800_000, "", day=16),
        OtherItem("Meta Ads", "Marketing", 400_000, 1_100_000, "", day=18),
        OtherItem("Eventbrite", "Marketing", 30000, 250000, "", n=2, prob=0.5),
        OtherItem("DoorDash", "Meals", 20000, 70000, "team lunch", n=8),
        OtherItem("Uber Eats", "Meals", 12000, 48000, "team lunch", n=8),
        OtherItem("Sweetgreen", "Meals", 9000, 36000, "team lunch", n=6),
        OtherItem("Delta", "Travel", 30000, 140000, "", n=3, prob=0.8),
        OtherItem("United Airlines", "Travel", 28000, 110000, "", n=2, prob=0.7),
        OtherItem("Marriott", "Travel", 24000, 90000, "", n=2, prob=0.7),
        OtherItem("Uber", "Travel", 1200, 9500, "", n=8),
        OtherItem("Cooley LLP", "Legal", 600000, 1_100_000, "", day=25,
                  months=(2, 5, 9)),
        OtherItem("Clerky", "Legal", 15000, 60000, "", day=11, months=(0, 6)),
    ),
    refunds=(
        (3, 18, "Figma", 36000, "Software", "refund: seat downgrade"),
        (6, 20, "Delta", 58000, "Travel", "refund: cancelled flight"),
        (9, 6, "Intercom", 74000, "Software", "refund: duplicate charge"),
        (10, 14, "United Airlines", 41000, "Travel", "refund: cancelled flight"),
    ),
    payouts_per_month=4,
    payout_days=(4, 11, 18, 25),
    payout_start=38_000_000,
    payout_growth=1.05,
    rent_cents=4_200_000,
    bank_fee_cents=4500,
)
