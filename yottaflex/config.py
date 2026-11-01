"""Central configuration and scale parameterization."""
from dataclasses import dataclass

SEED = 20261022
YEAR = 2026

# Transparent starting weights for candidate-slot ranking. Compatibility is
# enforced as a hard gate in slotting.slot_fit, before these factors are scored.
SLOT_SCORE_WEIGHTS = {
    "velocity": 0.30,
    "distance": 0.25,
    "capacity": 0.15,
    "replenishment": 0.15,
    "operational": 0.15,
}


@dataclass
class Scale:
    warehouses: int = 20
    locations: int = 10_000
    skus: int = 5_000
    order_lines: int = 100_000
    # starter dataset (default CLI flag --scale starter)
    @classmethod
    def starter(cls):
        return cls(warehouses=3, locations=400, skus=150, order_lines=2_000)


SCALE_FULL = Scale()
SCALE_STARTER = Scale.starter()
