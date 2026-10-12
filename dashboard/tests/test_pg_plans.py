"""Integration test: plans survive new connections and concurrent updates."""
import os
import threading
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pg_plan_store as store

class PlanDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.getenv("PLAN_DATABASE_URL", "").strip()
        if not url and os.getenv("RELAXDEV_DEPLOY") == "1":
            url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise unittest.SkipTest("Dedicated dashboard PostgreSQL is required")
        store.init_storage()

    def setUp(self):
        # Use only the dedicated test PostgreSQL DB!
        with store._connect() as db:
            db.execute("UPDATE fk_dashboard_plan_state SET payload='{}'::jsonb WHERE id=1")

    def test_save_history_delete_and_reconnect(self):
        store.save({"2026-10-12":{"line|bread":100}}, "plan.xlsx")
        store.save({"2026-10-13":{"line|bread":150}}, "plan2.xlsx")
        self.assertEqual(store.load()["2026-10-12"]["line|bread"],100)
        store.log_upload({"action":"upload","ok":True},200)
        self.assertEqual(store.history()[0]["action"],"upload")
        store.delete_dates(["2026-10-12"],200)
        self.assertNotIn("2026-10-12",store.load())
        self.assertEqual(store.load()["2026-10-13"]["line|bread"],150)

    def test_concurrent_updates(self):
        exceptions=[]
        def writer(day):
            try:
                store.save({day:{"line|bread":10}}, day)
            except Exception as exc:
                exceptions.append(exc)
        threads=[threading.Thread(target=writer,args=(f"2026-10-{day:02d}",)) for day in range(1,11)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertFalse(exceptions,exceptions)
        self.assertEqual(len(store.load()),10)

    def test_product_map_has_own_table(self):
        import plans
        import product_map
        plans.PG_PLANS = True
        product_map.init_storage()
        store.save({"2026-10-20":{"line|bread":5}}, "plan.xlsx")
        product_map.save({"bhm":{"Батон / ромашка":"Батон классический"}}, "map.xlsx", {"kc":{"Начинка":"Внесение начинки"}})
        self.assertEqual(product_map.load()["map"], {"bhm":{"Батон / ромашка":"Батон классический"}})
        self.assertEqual(product_map.load()["ops"], {"kc":{"Начинка":"Внесение начинки"}})
        self.assertEqual(store.load()["2026-10-20"]["line|bread"], 5)  # планы не задеты
        product_map.save({}, "")

if __name__=="__main__":
    unittest.main()
