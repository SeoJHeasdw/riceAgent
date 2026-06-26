-- Provider-specific extra fields for registered SaaS models, stored as a JSON
-- blob so new providers don't need a schema change each time.
--   openai (custom/OpenAI-compatible): { "base_url": "https://..." }
--   watsonx:                           { "base_url": "https://<region>.ml.cloud.ibm.com", "project_id": "..." }
ALTER TABLE provider_models ADD COLUMN extra TEXT;
