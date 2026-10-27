CREATE TABLE transactions (id INTEGER PRIMARY KEY, nsu TEXT NOT NULL, merchant TEXT NOT NULL, amount_cents INTEGER NOT NULL, occurred_at TEXT NOT NULL, status TEXT NOT NULL, card_last4 TEXT NOT NULL);
INSERT INTO transactions VALUES
  (1, 'A001', 'MERCADO', 1000, '2026-10-19T12:00:00+00:00', 'approved', '1001'),
  (2, 'B002', 'PADARIA', 2000, '2026-10-19T12:00:00+00:00', 'approved', '2002'),
  (3, 'D004', 'FARMACIA', 3000, '2026-10-19T12:00:00+00:00', 'approved', '4004'),
  (4, 'D004', 'FARMACIA', 3000, '2026-10-19T12:00:00+00:00', 'approved', '4004'),
  (5, 'E005', 'CAFE', 4000, '2026-10-19T12:00:00+00:00', 'approved', '5005'),
  (6, 'F006', 'CAFE', 4000, '2026-10-19T12:03:00+00:00', 'approved', '5005'),
  (7, 'G007', 'LIVRARIA', 5000, '2026-10-19T12:00:00+00:00', 'approved', '7007');
