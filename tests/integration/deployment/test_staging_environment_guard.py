import unittest


class StagingEnvironmentGuardTests(unittest.TestCase):
    def test_rejects_production_identifier_from_environment(self):
        from scripts.staging_environment_guard import validate_staging_environment

        with self.assertRaisesRegex(ValueError, "production identifier"):
            validate_staging_environment({
                "MYSQL_DATABASE": "causalagent_production",
                "COMPOSE_PROJECT_NAME": "rag-eval-staging",
                "STAGING_VOLUME_NAMES": "rag_eval_staging_mysql",
            })
