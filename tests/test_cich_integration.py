import unittest
import json
import os
import sys

# Add root directory to python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, ADMIN_EMAILS

class TestCICHIntegration(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()
        self.admin_email = "shawnmckenzie11.sm@gmail.com"
        
    def login_admin(self):
        with self.client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['email'] = self.admin_email

    def test_unauthorized_access(self):
        # 1. Accessing rules without auth should return 401
        res = self.client.get("/api/heuristics/rules")
        self.assertEqual(res.status_code, 401)
        
        # 2. Accessing test without auth should return 401
        res = self.client.post("/api/heuristics/test", json={})
        self.assertEqual(res.status_code, 401)

    def test_get_heuristics_rules(self):
        self.login_admin()
        res = self.client.get("/api/heuristics/rules")
        self.assertEqual(res.status_code, 200)
        rules = res.get_json()
        self.assertIn("publication_types", rules)
        self.assertIn("study_types", rules)
        self.assertIn("constants", rules)
        self.assertIn("extraction", rules)

    def test_dry_run_heuristics_test(self):
        self.login_admin()
        
        # Fetch current rules first
        res = self.client.get("/api/heuristics/rules")
        current_rules = res.get_json()
        
        # Dry-run test endpoint
        res = self.client.post("/api/heuristics/test", json=current_rules)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("score", data)
        self.assertGreater(data["score"], 0.0)

    def test_save_heuristics_rules_regression_gate(self):
        self.login_admin()
        
        # Fetch current rules first
        res = self.client.get("/api/heuristics/rules")
        current_rules = res.get_json()
        
        # Create a copy with intentionally broken rules (e.g. empty keyword lists) that should fail alignment
        bad_rules = json.loads(json.dumps(current_rules))
        bad_rules["publication_types"] = {}
        bad_rules["study_types"] = {}
        
        # Try to save bad rules -> should trigger 422 Unprocessable Entity regression gate block
        res = self.client.post("/api/heuristics/rules", json=bad_rules)
        self.assertEqual(res.status_code, 422)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertIn("Save blocked", data["error"])

    def test_backpopulation_task_lifecycle(self):
        self.login_admin()
        
        # 1. Trigger backpopulation
        res = self.client.post("/api/backpopulate")
        self.assertEqual(res.status_code, 202)
        data = res.get_json()
        self.assertIn("task_id", data)
        task_id = data["task_id"]
        
        # 2. Immediately check task status
        res = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(res.status_code, 200)
        task_data = res.get_json()
        self.assertEqual(task_data["task_id"], task_id)
        self.assertIn(task_data["status"], ["pending", "running", "completed", "failed"])

    def test_llm_rules_lifecycle(self):
        self.login_admin()
        
        # 1. Get LLM rules
        res = self.client.get("/api/llm/rules")
        self.assertEqual(res.status_code, 200)
        rules = res.get_json()
        self.assertIn("version", rules)
        self.assertIn("decision_nodes", rules)
        
        # 2. Test LLM rules (dry-run prompt compilation)
        res = self.client.post("/api/llm/test", json=rules)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("prompt_length", data)
        
        # 3. Test invalid LLM rules payload -> should return 422
        bad_rules = json.loads(json.dumps(rules))
        bad_rules["decision_nodes"] = "invalid_type_to_trigger_compilation_exception"
        
        res = self.client.post("/api/llm/test", json=bad_rules)
        self.assertEqual(res.status_code, 422)
        
        # 4. Save valid LLM rules
        res = self.client.post("/api/llm/rules", json=rules)
        self.assertEqual(res.status_code, 200)
        save_data = res.get_json()
        self.assertEqual(save_data["status"], "success")

    def test_llm_unauthorized_access(self):
        # Accessing LLM rules without auth should return 401
        res = self.client.get("/api/llm/rules")
        self.assertEqual(res.status_code, 401)
        
        # Accessing LLM test without auth should return 401
        res = self.client.post("/api/llm/test", json={})
        self.assertEqual(res.status_code, 401)

if __name__ == "__main__":
    unittest.main()
