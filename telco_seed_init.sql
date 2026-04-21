-- Telco mini sample database for PostgreSQL
-- Import:
--   psql -U <user> -d <db_name> -f telco_seed_init.sql

BEGIN;

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS usage_records CASCADE;
DROP TABLE IF EXISTS subscriptions CASCADE;
DROP TABLE IF EXISTS plans CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    customer_id      SERIAL PRIMARY KEY,
    full_name        VARCHAR(100) NOT NULL,
    phone_number     VARCHAR(20) UNIQUE NOT NULL,
    city             VARCHAR(50) NOT NULL,
    segment          VARCHAR(20) NOT NULL CHECK (segment IN ('prepaid', 'postpaid', 'business')),
    status           VARCHAR(20) NOT NULL CHECK (status IN ('active', 'inactive', 'suspended')),
    created_at       DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE plans (
    plan_id              SERIAL PRIMARY KEY,
    plan_name            VARCHAR(50) UNIQUE NOT NULL,
    monthly_fee          NUMERIC(10,2) NOT NULL CHECK (monthly_fee >= 0),
    data_limit_gb        NUMERIC(10,2) NOT NULL CHECK (data_limit_gb >= 0),
    voice_limit_minutes  INTEGER NOT NULL CHECK (voice_limit_minutes >= 0),
    sms_limit            INTEGER NOT NULL CHECK (sms_limit >= 0),
    plan_type            VARCHAR(20) NOT NULL CHECK (plan_type IN ('prepaid', 'postpaid', 'business'))
);

CREATE TABLE subscriptions (
    subscription_id   SERIAL PRIMARY KEY,
    customer_id       INTEGER NOT NULL REFERENCES customers(customer_id),
    plan_id           INTEGER NOT NULL REFERENCES plans(plan_id),
    msisdn            VARCHAR(20) UNIQUE NOT NULL,
    start_date        DATE NOT NULL,
    end_date          DATE,
    status            VARCHAR(20) NOT NULL CHECK (status IN ('active', 'cancelled', 'suspended')),
    CONSTRAINT chk_subscription_dates CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE TABLE usage_records (
    usage_id          SERIAL PRIMARY KEY,
    subscription_id   INTEGER NOT NULL REFERENCES subscriptions(subscription_id),
    usage_date        DATE NOT NULL,
    data_used_gb      NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (data_used_gb >= 0),
    call_minutes      INTEGER NOT NULL DEFAULT 0 CHECK (call_minutes >= 0),
    sms_count         INTEGER NOT NULL DEFAULT 0 CHECK (sms_count >= 0),
    roaming           BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE invoices (
    invoice_id        SERIAL PRIMARY KEY,
    customer_id       INTEGER NOT NULL REFERENCES customers(customer_id),
    billing_month     DATE NOT NULL,
    amount_due        NUMERIC(10,2) NOT NULL CHECK (amount_due >= 0),
    due_date          DATE NOT NULL,
    status            VARCHAR(20) NOT NULL CHECK (status IN ('paid', 'partial', 'unpaid', 'overdue')),
    created_at        DATE NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (customer_id, billing_month)
);

CREATE TABLE payments (
    payment_id        SERIAL PRIMARY KEY,
    invoice_id        INTEGER NOT NULL REFERENCES invoices(invoice_id),
    payment_date      DATE NOT NULL,
    amount_paid       NUMERIC(10,2) NOT NULL CHECK (amount_paid >= 0),
    method            VARCHAR(20) NOT NULL CHECK (method IN ('bank_transfer', 'card', 'cash', 'e_wallet')),
    status            VARCHAR(20) NOT NULL CHECK (status IN ('success', 'failed', 'pending'))
);

CREATE INDEX idx_subscriptions_customer_id ON subscriptions(customer_id);
CREATE INDEX idx_subscriptions_plan_id ON subscriptions(plan_id);
CREATE INDEX idx_usage_subscription_id ON usage_records(subscription_id);
CREATE INDEX idx_usage_date ON usage_records(usage_date);
CREATE INDEX idx_invoices_customer_id ON invoices(customer_id);
CREATE INDEX idx_payments_invoice_id ON payments(invoice_id);

INSERT INTO plans (plan_name, monthly_fee, data_limit_gb, voice_limit_minutes, sms_limit, plan_type) VALUES
('Flexi 5',      99000,  5,  100,  50,  'prepaid'),
('Flexi 15',    149000, 15,  300, 100,  'prepaid'),
('Smart 30',    199000, 30,  500, 200,  'postpaid'),
('Unlimited Max',299000,999, 1000, 500, 'postpaid'),
('Biz Share 50',499000, 50, 2000, 500,  'business'),
('Biz Unlimited',799000,999, 5000, 1000,'business');

INSERT INTO customers (full_name, phone_number, city, segment, status, created_at) VALUES
('Nguyen Van An',   '0901000001', 'Ha Noi',       'postpaid', 'active',    '2025-01-10'),
('Tran Thi Binh',   '0901000002', 'Ho Chi Minh',  'prepaid',  'active',    '2025-02-05'),
('Le Quang Huy',    '0901000003', 'Da Nang',      'postpaid', 'active',    '2025-02-18'),
('Pham Thu Ha',     '0901000004', 'Hai Phong',    'prepaid',  'inactive',  '2025-03-01'),
('Do Minh Khoa',    '0901000005', 'Can Tho',      'business', 'active',    '2025-03-12'),
('Vo Ngoc Lan',     '0901000006', 'Ha Noi',       'prepaid',  'active',    '2025-03-25'),
('Bui Thanh Nam',   '0901000007', 'Ho Chi Minh',  'postpaid', 'suspended', '2025-04-02'),
('Nguyen My Linh',  '0901000008', 'Da Nang',      'postpaid', 'active',    '2025-04-10'),
('Tran Duc Long',   '0901000009', 'Ha Noi',       'business', 'active',    '2025-04-15'),
('Hoang Gia Bao',   '0901000010', 'Nha Trang',    'prepaid',  'active',    '2025-04-20'),
('Dang Thu Trang',  '0901000011', 'Ho Chi Minh',  'postpaid', 'active',    '2025-05-01'),
('Phan Anh Tuan',   '0901000012', 'Hai Phong',    'prepaid',  'active',    '2025-05-08');

INSERT INTO subscriptions (customer_id, plan_id, msisdn, start_date, end_date, status) VALUES
(1, 3, '0987000001', '2025-01-10', NULL,         'active'),
(2, 1, '0987000002', '2025-02-05', NULL,         'active'),
(3, 4, '0987000003', '2025-02-18', NULL,         'active'),
(4, 2, '0987000004', '2025-03-01', '2025-05-20', 'cancelled'),
(5, 5, '0987000005', '2025-03-12', NULL,         'active'),
(6, 2, '0987000006', '2025-03-25', NULL,         'active'),
(7, 3, '0987000007', '2025-04-02', NULL,         'suspended'),
(8, 4, '0987000008', '2025-04-10', NULL,         'active'),
(9, 6, '0987000009', '2025-04-15', NULL,         'active'),
(10,1, '0987000010', '2025-04-20', NULL,         'active'),
(11,3, '0987000011', '2025-05-01', NULL,         'active'),
(12,2, '0987000012', '2025-05-08', NULL,         'active');

INSERT INTO usage_records (subscription_id, usage_date, data_used_gb, call_minutes, sms_count, roaming) VALUES
(1, '2025-05-01',  8.50, 120,  30, FALSE),
(1, '2025-05-15', 10.20, 140,  25, FALSE),
(1, '2025-06-01',  9.80, 110,  20, FALSE),
(1, '2025-06-15', 11.10, 150,  40, TRUE),
(2, '2025-05-03',  1.20,  25,  10, FALSE),
(2, '2025-05-20',  2.10,  40,  12, FALSE),
(2, '2025-06-05',  1.80,  35,   8, FALSE),
(3, '2025-05-02', 25.00, 220,  60, FALSE),
(3, '2025-05-18', 28.40, 260,  55, TRUE),
(3, '2025-06-02', 31.50, 280,  70, TRUE),
(4, '2025-04-10',  4.50,  55,  18, FALSE),
(4, '2025-05-10',  3.20,  42,  14, FALSE),
(5, '2025-05-05', 18.30, 600, 110, FALSE),
(5, '2025-05-25', 20.10, 720, 130, FALSE),
(5, '2025-06-05', 22.40, 680, 125, TRUE),
(6, '2025-05-07',  6.10,  75,  21, FALSE),
(6, '2025-06-07',  7.40,  82,  19, FALSE),
(7, '2025-05-09',  2.50,  30,   5, FALSE),
(7, '2025-06-09',  0.50,   5,   1, FALSE),
(8, '2025-05-11', 35.20, 310,  85, FALSE),
(8, '2025-06-11', 38.60, 330,  88, TRUE),
(9, '2025-05-13', 44.00, 900, 140, FALSE),
(9, '2025-06-13', 49.50, 980, 150, TRUE),
(10,'2025-05-14',  0.80,  15,   4, FALSE),
(10,'2025-06-14',  1.40,  18,   3, FALSE),
(11,'2025-05-16', 12.60, 145,  33, FALSE),
(11,'2025-06-16', 14.20, 160,  36, FALSE),
(12,'2025-05-17',  5.10,  70,  20, FALSE),
(12,'2025-06-17',  6.00,  74,  18, FALSE);

INSERT INTO invoices (customer_id, billing_month, amount_due, due_date, status, created_at) VALUES
(1,  '2025-05-01', 229000, '2025-05-20', 'paid',    '2025-05-01'),
(1,  '2025-06-01', 249000, '2025-06-20', 'paid',    '2025-06-01'),
(2,  '2025-05-01',  99000, '2025-05-20', 'paid',    '2025-05-01'),
(2,  '2025-06-01', 109000, '2025-06-20', 'partial', '2025-06-01'),
(3,  '2025-05-01', 329000, '2025-05-20', 'paid',    '2025-05-01'),
(3,  '2025-06-01', 359000, '2025-06-20', 'overdue', '2025-06-01'),
(4,  '2025-05-01', 149000, '2025-05-20', 'unpaid',  '2025-05-01'),
(5,  '2025-05-01', 599000, '2025-05-20', 'paid',    '2025-05-01'),
(5,  '2025-06-01', 649000, '2025-06-20', 'paid',    '2025-06-01'),
(6,  '2025-05-01', 149000, '2025-05-20', 'paid',    '2025-05-01'),
(6,  '2025-06-01', 159000, '2025-06-20', 'paid',    '2025-06-01'),
(7,  '2025-05-01', 199000, '2025-05-20', 'overdue', '2025-05-01'),
(8,  '2025-05-01', 349000, '2025-05-20', 'paid',    '2025-05-01'),
(8,  '2025-06-01', 389000, '2025-06-20', 'partial', '2025-06-01'),
(9,  '2025-05-01', 899000, '2025-05-20', 'paid',    '2025-05-01'),
(9,  '2025-06-01', 949000, '2025-06-20', 'paid',    '2025-06-01'),
(10, '2025-05-01',  99000, '2025-05-20', 'paid',    '2025-05-01'),
(11, '2025-05-01', 219000, '2025-05-20', 'paid',    '2025-05-01'),
(11, '2025-06-01', 239000, '2025-06-20', 'unpaid',  '2025-06-01'),
(12, '2025-05-01', 149000, '2025-05-20', 'paid',    '2025-05-01');

INSERT INTO payments (invoice_id, payment_date, amount_paid, method, status) VALUES
(1,  '2025-05-10', 229000, 'bank_transfer', 'success'),
(2,  '2025-06-15', 249000, 'card',          'success'),
(3,  '2025-05-18',  99000, 'e_wallet',      'success'),
(4,  '2025-06-18',  50000, 'e_wallet',      'success'),
(5,  '2025-05-19', 329000, 'bank_transfer', 'success'),
(6,  '2025-06-25',      0, 'bank_transfer', 'failed'),
(8,  '2025-05-16', 599000, 'bank_transfer', 'success'),
(9,  '2025-06-18', 649000, 'card',          'success'),
(10, '2025-05-17', 149000, 'cash',          'success'),
(11, '2025-06-17', 159000, 'e_wallet',      'success'),
(12, '2025-05-29', 100000, 'cash',          'pending'),
(13, '2025-05-18', 349000, 'bank_transfer', 'success'),
(14, '2025-06-19', 200000, 'card',          'success'),
(15, '2025-05-15', 899000, 'bank_transfer', 'success'),
(16, '2025-06-15', 949000, 'bank_transfer', 'success'),
(17, '2025-05-14',  99000, 'e_wallet',      'success'),
(18, '2025-05-19', 219000, 'card',          'success'),
(20, '2025-05-20', 149000, 'cash',          'success');

COMMIT;

-- Quick sanity queries:
-- SELECT COUNT(*) FROM customers;
-- SELECT COUNT(*) FROM plans;
-- SELECT COUNT(*) FROM subscriptions;
-- SELECT COUNT(*) FROM usage_records;
-- SELECT COUNT(*) FROM invoices;
-- SELECT COUNT(*) FROM payments;
