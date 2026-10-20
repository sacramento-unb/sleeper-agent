import tempfile
import time
import unittest
from pathlib import Path
from storage.db import Database, hash_password, verify_password, ADMIN_EMAIL


class TestStorageDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_password_hashing_and_salting(self):
        pw = "SuperSecret123"
        h1, s1 = hash_password(pw)
        h2, s2 = hash_password(pw)

        # Salts must be different and random
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(h1, h2)

        # Both must verify correctly
        self.assertTrue(verify_password(pw, h1, s1))
        self.assertTrue(verify_password(pw, h2, s2))
        self.assertFalse(verify_password("WrongPassword", h1, s1))

    def test_admin_seeding(self):
        users = self.db.list_users()
        self.assertEqual(len(users), 1)
        admin = users[0]
        self.assertEqual(admin["email"], ADMIN_EMAIL.lower())
        self.assertTrue(admin["is_admin"])
        self.assertEqual(admin["status"], "pending_password")

    def test_first_access_flow(self):
        # Admin sets first access password
        res = self.db.set_first_access_password(ADMIN_EMAIL, "adminpass123")
        self.assertEqual(res["status"], "active")
        self.assertTrue(res["session_token"])

        # Second attempt must be rejected
        with self.assertRaises(ValueError):
            self.db.set_first_access_password(ADMIN_EMAIL, "anotherpass")

        # Session token should be valid
        session = self.db.get_session(res["session_token"])
        self.assertIsNotNone(session)
        self.assertEqual(session["email"], ADMIN_EMAIL)
        self.assertTrue(session["is_admin"])

        # Login with correct credentials
        auth_res = self.db.authenticate_user(ADMIN_EMAIL, "adminpass123")
        self.assertEqual(auth_res["status"], "active")

        # Login with incorrect credentials
        with self.assertRaises(ValueError):
            self.db.authenticate_user(ADMIN_EMAIL, "wrongpass")

    def test_unregistered_email_rejection(self):
        # Email not pre-registered cannot activate first access
        unregistered = "intruder@domain.com"
        with self.assertRaises(ValueError) as ctx:
            self.db.set_first_access_password(unregistered, "password123")
        self.assertIn("não foi pré-cadastrado pelo administrador", str(ctx.exception))

        # Email not pre-registered cannot login
        with self.assertRaises(ValueError) as ctx2:
            self.db.authenticate_user(unregistered, "password123")
        self.assertIn("não está cadastrado", str(ctx2.exception))

    def test_admin_register_user_and_user_first_access(self):
        # Admin registers new user
        new_email = "cliente@persone.com"
        reg = self.db.register_user(new_email)
        self.assertEqual(reg["status"], "pending_password")
        self.assertFalse(reg["is_admin"])

        # Attempt to login before setting password must fail with clear message
        with self.assertRaises(ValueError) as ctx:
            self.db.authenticate_user(new_email, "somepassword")
        self.assertIn("Primeiro acesso pendente", str(ctx.exception))

        # User completes first access
        activated = self.db.set_first_access_password(new_email, "clientepass123")
        self.assertEqual(activated["status"], "active")

        # User can now login
        auth = self.db.authenticate_user(new_email, "clientepass123")
        self.assertFalse(auth["is_admin"])
        self.assertTrue(auth["session_token"])

    def test_password_reset_token_flow(self):
        email = "reset_user@persone.com"
        self.db.register_user(email)
        self.db.set_first_access_password(email, "oldpass123")

        # Admin generates reset token
        token = self.db.create_password_reset_token(email)
        self.assertTrue(token)

        # Reset password with token
        res = self.db.reset_password_with_token(token, "newpass123")
        self.assertTrue(res)

        # Login with new password
        auth = self.db.authenticate_user(email, "newpass123")
        self.assertEqual(auth["email"], email)

        # Old password must fail
        with self.assertRaises(ValueError):
            self.db.authenticate_user(email, "oldpass123")

        # Using the same token again must fail
        with self.assertRaises(ValueError):
            self.db.reset_password_with_token(token, "attempt_again")

    def test_admin_direct_reset(self):
        email = "direct_reset@persone.com"
        self.db.register_user(email)
        self.db.set_first_access_password(email, "firstpass123")

        self.db.admin_reset_user_password(email, "admin_assigned_pass123")
        auth = self.db.authenticate_user(email, "admin_assigned_pass123")
        self.assertEqual(auth["email"], email)

    def test_query_caching(self):
        key = "test_key_1"
        data = {"team": "Persone", "score": 114.5, "starters": ["Hurts", "Cook"]}

        # Cache miss
        self.assertIsNone(self.db.get_cached_query(key))

        # Cache store
        self.db.set_cached_query(key, data, ttl_seconds=10)
        cached = self.db.get_cached_query(key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["team"], data["team"])
        self.assertEqual(cached["score"], data["score"])
        self.assertTrue(cached.get("cached"))
        self.assertIn("cache_age_seconds", cached)

        # Expiration
        self.db.set_cached_query("expire_key", {"val": 1}, ttl_seconds=-1)
        self.assertIsNone(self.db.get_cached_query("expire_key"))

        # Clear cache
        cleared = self.db.clear_cache()
        self.assertGreaterEqual(cleared, 1)
        self.assertIsNone(self.db.get_cached_query(key))


if __name__ == "__main__":
    unittest.main()
