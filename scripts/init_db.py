"""首次启动初始化：建表 + 导入演示数据。"""
from app.db import init_db
from app.services.seed import seed_all

if __name__ == "__main__":
    init_db()
    seed_all()
    print("seed done")
