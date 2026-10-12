"""EV-2: the eval toolkit -- gold sets from an expert's own files (`load_gold_set`), a scorer per capability kind
(`score_*`); a runner follows."""
from rag_wright.evals.gold import GoldImportError, GoldMapping, aload_gold_set, load_gold_set
from rag_wright.evals.scorers import (lenient_match, score_candidate_choice, score_classification, score_extraction,
                                      score_generation, score_ingestion, score_judgment, score_relational,
                                      score_retrieval)

__all__ = ["GoldImportError", "GoldMapping", "aload_gold_set", "lenient_match", "load_gold_set",
           "score_candidate_choice", "score_classification", "score_extraction", "score_generation", "score_ingestion",
           "score_judgment", "score_relational", "score_retrieval"]
