"""Test path-based BFS with a realistic 50-table Telco DB."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.schema_graph import SchemaGraph, select_tables
from core.query_context import selective_schema_context_text, schema_context_text

# =====================================================
# Simulated 50-table Telco Database
# =====================================================
TABLES = {
    # --- Core Customer ---
    "customers":          "public.customers:\n  cust_id bigint, cust_name text, email text, phone text, cust_type_id bigint, segment_id bigint",
    "customer_types":     "public.customer_types:\n  cust_type_id bigint, type_name text, description text",
    "customer_segments":  "public.customer_segments:\n  segment_id bigint, segment_name text, min_arpu double, max_arpu double",
    "customer_addresses": "public.customer_addresses:\n  addr_id bigint, cust_id bigint, address text, city text, district text, province_id bigint",
    "customer_contacts":  "public.customer_contacts:\n  contact_id bigint, cust_id bigint, contact_type text, contact_value text",
    "customer_documents": "public.customer_documents:\n  doc_id bigint, cust_id bigint, doc_type text, doc_number text, issued_date text",
    "customer_kyc":       "public.customer_kyc:\n  kyc_id bigint, cust_id bigint, kyc_status text, kyc_date text, agent_id bigint",

    # --- Subscriber / SIM ---
    "subscribers":        "public.subscribers:\n  sub_id bigint, cust_id bigint, isdn bigint, status bigint, pack_id bigint, sim_id bigint",
    "sim_cards":          "public.sim_cards:\n  sim_id bigint, serial_number text, sim_type text, status text, activated_at text",
    "subscriber_history": "public.subscriber_history:\n  hist_id bigint, sub_id bigint, action text, old_status bigint, new_status bigint, changed_at text",

    # --- Packages / Plans ---
    "packages":           "public.packages:\n  pack_id bigint, pack_name text, pack_type text, price double, data_gb double, voice_min bigint",
    "package_features":   "public.package_features:\n  feature_id bigint, pack_id bigint, feature_name text, feature_value text",
    "package_promotions": "public.package_promotions:\n  promo_id bigint, pack_id bigint, discount_pct double, start_date text, end_date text",
    "pack_categories":    "public.pack_categories:\n  cat_id bigint, cat_name text, description text",

    # --- Billing / Invoices ---
    "invoices":           "public.invoices:\n  inv_id bigint, cust_id bigint, billing_cycle text, amount_due double, status text, due_date text",
    "invoice_lines":      "public.invoice_lines:\n  line_id bigint, inv_id bigint, description text, amount double, tax double",
    "payments":           "public.payments:\n  pay_id bigint, inv_id bigint, amount double, pay_date text, pay_method text, status text",
    "payment_methods":    "public.payment_methods:\n  method_id bigint, method_name text, provider text",
    "credit_notes":       "public.credit_notes:\n  cn_id bigint, inv_id bigint, amount double, reason text, created_at text",
    "billing_cycles":     "public.billing_cycles:\n  cycle_id bigint, cycle_name text, start_date text, end_date text",

    # --- Usage / CDR ---
    "cdr_voice":          "public.cdr_voice:\n  cdr_id bigint, sub_id bigint, call_type text, duration bigint, cost double, call_date text",
    "cdr_data":           "public.cdr_data:\n  cdr_id bigint, sub_id bigint, bytes_used bigint, session_start text, session_end text",
    "cdr_sms":            "public.cdr_sms:\n  cdr_id bigint, sub_id bigint, sms_type text, dest_number text, sent_at text",
    "usage_summary":      "public.usage_summary:\n  summary_id bigint, sub_id bigint, month text, total_voice bigint, total_data bigint, total_sms bigint",

    # --- Network / Infrastructure ---
    "base_stations":      "public.base_stations:\n  bs_id bigint, bs_name text, latitude double, longitude double, region_id bigint",
    "regions":            "public.regions:\n  region_id bigint, region_name text, province_id bigint",
    "provinces":          "public.provinces:\n  province_id bigint, province_name text, country text",
    "network_outages":    "public.network_outages:\n  outage_id bigint, bs_id bigint, start_time text, end_time text, severity text",

    # --- Products / Services ---
    "products":           "public.products:\n  prod_id bigint, prod_name text, category_id bigint, price double",
    "product_categories": "public.product_categories:\n  category_id bigint, cat_name text, parent_cat_id bigint",
    "orders":             "public.orders:\n  order_id bigint, cust_id bigint, order_date text, total_amount double, status text",
    "order_items":        "public.order_items:\n  item_id bigint, order_id bigint, prod_id bigint, quantity bigint, unit_price double",

    # --- Support / Tickets ---
    "tickets":            "public.tickets:\n  ticket_id bigint, cust_id bigint, sub_id bigint, category text, priority text, status text, created_at text",
    "ticket_comments":    "public.ticket_comments:\n  comment_id bigint, ticket_id bigint, agent_id bigint, content text, created_at text",
    "ticket_categories":  "public.ticket_categories:\n  cat_id bigint, cat_name text, sla_hours bigint",

    # --- Agents / Staff ---
    "agents":             "public.agents:\n  agent_id bigint, agent_name text, department_id bigint, role text, email text",
    "departments":        "public.departments:\n  department_id bigint, dept_name text, manager_id bigint",
    "agent_performance":  "public.agent_performance:\n  perf_id bigint, agent_id bigint, month text, tickets_resolved bigint, avg_rating double",

    # --- Loyalty / Points ---
    "loyalty_accounts":   "public.loyalty_accounts:\n  loyalty_id bigint, cust_id bigint, points_balance bigint, tier text",
    "loyalty_transactions": "public.loyalty_transactions:\n  txn_id bigint, loyalty_id bigint, points bigint, txn_type text, description text, created_at text",
    "loyalty_tiers":      "public.loyalty_tiers:\n  tier_id bigint, tier_name text, min_points bigint, benefits text",

    # --- Campaign / Marketing ---
    "campaigns":          "public.campaigns:\n  campaign_id bigint, campaign_name text, channel text, start_date text, end_date text, budget double",
    "campaign_targets":   "public.campaign_targets:\n  target_id bigint, campaign_id bigint, segment_id bigint, target_count bigint",
    "campaign_results":   "public.campaign_results:\n  result_id bigint, campaign_id bigint, responses bigint, conversions bigint, revenue double",

    # --- Audit / Logs ---
    "audit_logs":         "public.audit_logs:\n  log_id bigint, table_name text, record_id bigint, action text, user_id bigint, created_at text",
    "system_configs":     "public.system_configs:\n  config_id bigint, config_key text, config_value text, updated_at text",
    "notifications":      "public.notifications:\n  notif_id bigint, cust_id bigint, channel text, message text, sent_at text, status text",

    # --- Dealer / Partner ---
    "dealers":            "public.dealers:\n  dealer_id bigint, dealer_name text, region_id bigint, contact_phone text",
    "dealer_commissions": "public.dealer_commissions:\n  comm_id bigint, dealer_id bigint, sub_id bigint, amount double, month text",
}

RELATIONSHIPS = [
    # Customer core
    "public.customers.cust_type_id <-> public.customer_types.cust_type_id (inferred)",
    "public.customers.segment_id <-> public.customer_segments.segment_id (inferred)",
    "public.customers.cust_id <-> public.customer_addresses.cust_id (inferred)",
    "public.customers.cust_id <-> public.customer_contacts.cust_id (inferred)",
    "public.customers.cust_id <-> public.customer_documents.cust_id (inferred)",
    "public.customers.cust_id <-> public.customer_kyc.cust_id (inferred)",
    # Subscriber
    "public.customers.cust_id <-> public.subscribers.cust_id (inferred)",
    "public.subscribers.pack_id <-> public.packages.pack_id (inferred)",
    "public.subscribers.sim_id <-> public.sim_cards.sim_id (inferred)",
    "public.subscribers.sub_id <-> public.subscriber_history.sub_id (inferred)",
    # Packages
    "public.packages.pack_id <-> public.package_features.pack_id (inferred)",
    "public.packages.pack_id <-> public.package_promotions.pack_id (inferred)",
    # Billing
    "public.customers.cust_id <-> public.invoices.cust_id (inferred)",
    "public.invoices.inv_id <-> public.invoice_lines.inv_id (inferred)",
    "public.invoices.inv_id <-> public.payments.inv_id (inferred)",
    "public.invoices.inv_id <-> public.credit_notes.inv_id (inferred)",
    # Usage
    "public.subscribers.sub_id <-> public.cdr_voice.sub_id (inferred)",
    "public.subscribers.sub_id <-> public.cdr_data.sub_id (inferred)",
    "public.subscribers.sub_id <-> public.cdr_sms.sub_id (inferred)",
    "public.subscribers.sub_id <-> public.usage_summary.sub_id (inferred)",
    # Network
    "public.base_stations.region_id <-> public.regions.region_id (inferred)",
    "public.regions.province_id <-> public.provinces.province_id (inferred)",
    "public.customer_addresses.province_id <-> public.provinces.province_id (inferred)",
    "public.base_stations.bs_id <-> public.network_outages.bs_id (inferred)",
    # Products & Orders
    "public.customers.cust_id <-> public.orders.cust_id (inferred)",
    "public.orders.order_id <-> public.order_items.order_id (inferred)",
    "public.order_items.prod_id <-> public.products.prod_id (inferred)",
    "public.products.category_id <-> public.product_categories.category_id (inferred)",
    # Support
    "public.customers.cust_id <-> public.tickets.cust_id (inferred)",
    "public.subscribers.sub_id <-> public.tickets.sub_id (inferred)",
    "public.tickets.ticket_id <-> public.ticket_comments.ticket_id (inferred)",
    "public.ticket_comments.agent_id <-> public.agents.agent_id (inferred)",
    # Agents
    "public.agents.department_id <-> public.departments.department_id (inferred)",
    "public.agents.agent_id <-> public.agent_performance.agent_id (inferred)",
    "public.agents.agent_id <-> public.customer_kyc.agent_id (inferred)",
    # Loyalty
    "public.customers.cust_id <-> public.loyalty_accounts.cust_id (inferred)",
    "public.loyalty_accounts.loyalty_id <-> public.loyalty_transactions.loyalty_id (inferred)",
    # Campaign
    "public.campaign_targets.segment_id <-> public.customer_segments.segment_id (inferred)",
    "public.campaigns.campaign_id <-> public.campaign_targets.campaign_id (inferred)",
    "public.campaigns.campaign_id <-> public.campaign_results.campaign_id (inferred)",
    # Notifications
    "public.customers.cust_id <-> public.notifications.cust_id (inferred)",
    # Dealers
    "public.dealers.region_id <-> public.regions.region_id (inferred)",
    "public.dealer_commissions.dealer_id <-> public.dealers.dealer_id (inferred)",
    "public.dealer_commissions.sub_id <-> public.subscribers.sub_id (inferred)",
]

snapshot = {
    "schema_name": "public",
    "table_list_text": "\n".join(TABLES.keys()),
    "tables": TABLES,
    "relationships": RELATIONSHIPS,
}

graph = SchemaGraph.from_snapshot(snapshot)
print(f"=== Telco DB: {len(graph.tables)} tables, {len(graph.edges)} edges ===\n")

# Full context size
full = schema_context_text(snapshot)
print(f"Full context: {len(full)} chars (~{len(full)//4} tokens)\n")

# Test queries
tests = [
    ("Tìm khách hàng Lê Tâm Linh",                       "Đơn giản: 1 root"),
    ("Thanh toán của khách hàng Nguyễn Văn A",             "Cross-branch: customer → invoices → payments"),
    ("Gói cước của thuê bao 0912345678",                   "Cross-branch: subscriber → packages"),
    ("Doanh thu từ chiến dịch marketing tháng 5",          "Campaign domain"),
    ("Khách hàng nào có điểm loyalty cao nhất",            "Loyalty domain"),
    ("Lịch sử cuộc gọi của thuê bao 0987654321",          "CDR voice"),
    ("Hoa don chua thanh toan cua khach hang VIP",         "Billing + segment"),
    ("Đại lý nào có hoa hồng cao nhất tháng này",         "Dealer domain"),
    ("Ticket chưa giải quyết của khách hàng",              "Support domain"),
    ("Agent nào giải quyết nhiều ticket nhất",             "Agent performance → tickets"),
    ("Sản phẩm bán chạy nhất theo đơn hàng",              "Products → orders"),
    ("Khách hàng ở Hà Nội có bao nhiêu thuê bao",         "Address → province → customer → subscriber"),
    ("Khuyến mãi gói cước cho phân khúc VIP",             "Packages → promotions + segments"),
]

print(f"{'='*80}")
print(f"{'Câu hỏi':<55} {'Tables':>5} {'Chars':>7} {'Save%':>6}")
print(f"{'='*80}")

for q, desc in tests:
    selected = select_tables(q, graph)
    sel_ctx = selective_schema_context_text(q, snapshot)
    savings = 100 - len(sel_ctx) * 100 // len(full)
    print(f"{q:<55} {len(selected):>5} {len(sel_ctx):>7} {savings:>5}%")
    print(f"  → {desc}")
    print(f"  → Tables: {selected}")
    
    # Show shortest paths for multi-match
    if len(selected) >= 2:
        for i, t1 in enumerate(selected):
            for t2 in selected[i+1:]:
                path = graph.shortest_path(t1, t2)
                if path and len(path) > 2:
                    print(f"  → Bridge: {' → '.join(path)}")
    print()

print(f"{'='*80}")
print(f"Full inject:      {len(full):>7} chars (~{len(full)//4} tokens)")
avg_sel = sum(len(selective_schema_context_text(q, snapshot)) for q, _ in tests) // len(tests)
print(f"Avg selective:    {avg_sel:>7} chars (~{avg_sel//4} tokens)")
print(f"Avg savings:      {100 - avg_sel * 100 // len(full):>6}%")
