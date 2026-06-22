-- 导入至 Tencent Cloud SQLite/MySQL 数据库的 licenses 表
BEGIN TRANSACTION;
INSERT INTO licenses (key, type, hwid, status) VALUES ('9358B22EEB7D46FE9361BD67836425A2', 'trial', '', 'unused');
INSERT INTO licenses (key, type, hwid, status) VALUES ('999EDA90E98B4C83913BEACC81A1EC84', 'trial', '', 'unused');
COMMIT;
