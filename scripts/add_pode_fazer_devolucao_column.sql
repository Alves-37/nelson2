-- Adds missing pode_fazer_devolucao column to usuarios table if it does not exist
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS pode_fazer_devolucao BOOLEAN DEFAULT FALSE;
