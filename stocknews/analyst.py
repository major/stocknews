"""Functions for handling analyst data."""

import re
from dataclasses import dataclass


@dataclass
class AnalystNews:
    """Parse analyst news so we can notify on changes."""

    headline: str
    firm: str = ""
    action: str = ""
    guidance: str = ""
    stock: str = ""
    price_target_action: str = ""
    price_target: float = 0

    def __post_init__(self) -> None:
        """Parse the headline after initialization."""
        self.parse_headline()

    def parse_headline(self) -> None:
        """Parse the analyst news headline."""
        self.parse_analyst_action()
        self.parse_price_target()

    def __repr__(self) -> str:  # pragma: no cover
        """Return a nicely formatted string representation of the instance."""
        return (
            f"AnalystNews("
            f"headline={self.headline!r}, "
            f"firm={self.firm!r}, "
            f"action={self.action!r}, "
            f"guidance={self.guidance!r}, "
            f"stock={self.stock!r}, "
            f"price_target_action={self.price_target_action!r}, "
            f"price_target={self.price_target!r})"
        )

    def parse_analyst_action(self) -> None:
        """Parse the analyst action in the first half of the headline."""
        action_patterns = [
            (
                r"^([\w\s]+) (Maintains|Reiterates) (.*) on (.+),",
                ["firm", "action", "guidance", "stock"],
            ),
            (
                r"^([\w\s]+) (Downgrades|Upgrades|Initiates Coverage) (?:on\s)*([\w\s]+) (?:with|to) (.+),",
                ["firm", "action", "stock", "guidance"],
            ),
        ]
        for regex, groups in action_patterns:
            match = re.match(regex, self.headline, re.IGNORECASE)
            if match:
                self.assign_groups(match.groups(), groups)
                if self.guidance and self.guidance.endswith("Rating"):
                    self.guidance = self.guidance.rstrip("Rating").strip()
                break

    def assign_groups(self, values: tuple, attributes: list) -> None:
        """Assign regex match groups to class attributes."""
        for attr, value in zip(attributes, values):
            setattr(self, attr, value)

    def parse_price_target(self) -> None:
        """Parse the price target change from the second half of the headline."""
        self.price_target = float(self.extract_value(r"\$([\d\.]+)"))
        self.price_target_action = self.extract_value(
            r", (Lowers|Maintains|Raises|Announces)"
        )

    def extract_value(self, pattern: str) -> str:
        """Helper to extract a single value using regex."""
        match = re.search(pattern, self.headline)
        return match.group(1) if match else ""
