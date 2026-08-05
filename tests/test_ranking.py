from __future__ import annotations

import unittest
from datetime import date

from papertrail.ranking import rank_group_papers


class RankingTests(unittest.TestCase):
    def test_profile_reorders_members_without_changing_membership(self) -> None:
        papers = [
            {
                "paper_id": "adaptive",
                "title": "Adaptive Tool Interfaces for Agents",
                "abstract": "Agents recover when tool schemas change.",
                "published_date": "2026-05-01",
                "similarity": 0.6,
                "citation_count": None,
                "is_new": False,
            },
            {
                "paper_id": "unrelated",
                "title": "Generic Prompt Benchmark",
                "abstract": "A broad benchmark.",
                "published_date": "2026-08-03",
                "similarity": 0.6,
                "citation_count": None,
                "is_new": True,
            },
        ]
        ranked = rank_group_papers(
            papers,
            {"active": True, "positive_labels": ["adaptive tool interfaces"], "negative_labels": []},
            today=date(2026, 8, 4),
        )
        self.assertEqual({item["paper_id"] for item in ranked}, {"adaptive", "unrelated"})
        self.assertEqual(ranked[0]["paper_id"], "adaptive")
        self.assertIn("Strong match to your interests", ranked[0]["ranking"]["reasons"])

    def test_recent_papers_get_neutral_citation_score_and_stable_ties(self) -> None:
        papers = [
            {
                "paper_id": paper_id,
                "title": title,
                "abstract": "",
                "published_date": "2026-08-01",
                "similarity": 0.5,
                "citation_count": 0,
                "is_new": True,
            }
            for paper_id, title in (("b", "Beta"), ("a", "Alpha"))
        ]
        ranked = rank_group_papers(papers, {"active": False}, today=date(2026, 8, 4))
        self.assertEqual([item["paper_id"] for item in ranked], ["a", "b"])
        self.assertTrue(
            all(item["ranking"]["components"]["citation_impact"] == 0.5 for item in ranked)
        )
