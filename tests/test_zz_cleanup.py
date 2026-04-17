import unittest

import database


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_shared_database_connection(self):
        await database.close_db()
