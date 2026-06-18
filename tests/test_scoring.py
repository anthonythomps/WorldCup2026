import unittest

from models import Match
from scoring import compute_team_records, rank_best_teams, rank_people, rank_worst_teams


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.draw = {
            "Alice": ["Alpha", "Bravo", "Charlie", "Delta"],
            "Ben": ["Echo", "Foxtrot", "Golf", "Hotel"],
        }

    def test_team_points_and_goal_difference(self):
        matches = [
            Match("Alpha", "Echo", 2, 0, stage="Group Stage"),
            Match("Bravo", "Foxtrot", 1, 1, stage="Group Stage"),
            Match("Charlie", "Golf", 0, 3, stage="Group Stage"),
        ]

        records = {record.name: record for record in compute_team_records(matches, self.draw)}

        self.assertEqual(records["Alpha"].points, 3)
        self.assertEqual(records["Alpha"].goal_difference, 2)
        self.assertEqual(records["Bravo"].points, 1)
        self.assertEqual(records["Charlie"].points, 0)
        self.assertEqual(records["Charlie"].goal_difference, -3)

    def test_worst_and_best_rankings(self):
        matches = [
            Match("Alpha", "Echo", 2, 0, stage="Group Stage"),
            Match("Charlie", "Golf", 0, 3, stage="Group Stage"),
            Match("Delta", "Hotel", 0, 2, stage="Group Stage"),
        ]

        records = compute_team_records(matches, self.draw)

        self.assertEqual(rank_best_teams(records)[0].name, "Golf")
        self.assertEqual(rank_worst_teams(records)[0].name, "Charlie")

    def test_people_combined_record(self):
        matches = [
            Match("Alpha", "Echo", 2, 0, stage="Group Stage"),
            Match("Bravo", "Foxtrot", 1, 1, stage="Group Stage"),
            Match("Charlie", "Golf", 0, 3, stage="Group Stage"),
            Match("Delta", "Hotel", 0, 2, stage="Group Stage"),
        ]

        people = rank_people(compute_team_records(matches, self.draw), self.draw)

        self.assertEqual(people[0].name, "Ben")
        self.assertEqual(people[0].points, 7)
        self.assertEqual(people[1].name, "Alice")
        self.assertEqual(people[1].points, 4)


if __name__ == "__main__":
    unittest.main()
