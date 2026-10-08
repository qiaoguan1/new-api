"""Video state transactions release their SQLite resource at context exit."""
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from store import Store


class VideoStoreLifecycleTests(unittest.TestCase):
    def test_context_commits_data_and_closes_database_handle(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Store(pathlib.Path(directory))
            with store.connect() as db:
                db.execute('create table lifecycle_fixture(value text)')
                db.execute("insert into lifecycle_fixture values('committed')")
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('select 1')
            with store.connect() as reopened:
                self.assertEqual(reopened.execute('select value from lifecycle_fixture').fetchone()[0], 'committed')


if __name__ == '__main__':
    unittest.main()
