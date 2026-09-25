"""Diagnosis engine: KPI patterns -> RF problem classes with evidence."""

from rfopt.diagnosis.models import Diagnosis, PROBLEM_CLASSES
from rfopt.diagnosis.engine import diagnose_entity, diagnose_frame, RULES

__all__ = ["Diagnosis", "PROBLEM_CLASSES", "diagnose_entity", "diagnose_frame",
           "RULES"]
