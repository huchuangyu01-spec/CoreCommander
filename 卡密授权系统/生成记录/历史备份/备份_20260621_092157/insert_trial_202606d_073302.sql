-- 导入至 Tencent Cloud SQLite/MySQL 数据库的 licenses 表
BEGIN TRANSACTION;
INSERT INTO licenses (key, type, hwid, status) VALUES ('23A9D1EFFE99B41A9A5577C64AC86752', 'trial', '', 'unused');
INSERT INTO licenses (key, type, hwid, status) VALUES ('FFB2E6D449DC5C879FD1CE0C182A63FF', 'trial', '', 'unused');
INSERT INTO licenses (key, type, hwid, status) VALUES ('AD89170573C576567C607DE897EE544B', 'trial', '', 'unused');
INSERT INTO licenses (key, type, hwid, status) VALUES ('DDDD19EA282ACA9573BBDCB48ACB5437', 'trial', '', 'unused');
INSERT INTO licenses (key, type, hwid, status) VALUES ('16B3D0F1A32E0FCD0F8A8459329472EF', 'trial', '', 'unused');
COMMIT;
