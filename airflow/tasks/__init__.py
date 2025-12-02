"""Airflow task modules with clear separation of concerns.

This package contains reusable task logic for the profile scoring pipeline.
Each module handles a specific domain:

- keywords: Keyword sampling and validation
- search: Profile search API calls
- storage: Database persistence operations
- llm_scoring: LLM-based profile labeling
- config: Shared configuration and types
"""

from tasks.config import PipelineConfig, get_config

__all__ = ["PipelineConfig", "get_config"]
