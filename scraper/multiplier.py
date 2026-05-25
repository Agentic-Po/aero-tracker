"""Multiplier math, preserved from the original aero-multiplier.sh."""

from __future__ import annotations

from dataclasses import dataclass


SIMULATION_STEPS_USD = (1_000, 25_000, 50_000, 100_000)
WARN_THRESHOLD = 1.1


@dataclass
class MultiplierResult:
    new_emissions: float
    aero_price_usd: float
    emissions_value: float
    total_rewards: float
    multiplier: float
    sim_plus_1k: float
    sim_plus_25k: float
    sim_plus_50k: float
    sim_plus_100k: float

    def to_dict(self) -> dict:
        return {
            "new_emissions": self.new_emissions,
            "aero_price_usd": self.aero_price_usd,
            "emissions_value": self.emissions_value,
            "total_rewards": self.total_rewards,
            "multiplier": self.multiplier,
            "sim_plus_1k": self.sim_plus_1k,
            "sim_plus_25k": self.sim_plus_25k,
            "sim_plus_50k": self.sim_plus_50k,
            "sim_plus_100k": self.sim_plus_100k,
        }


def compute(
    new_emissions: float,
    aero_price_usd: float,
    total_rewards: float,
) -> MultiplierResult:
    if total_rewards <= 0:
        raise ValueError(f"total_rewards must be > 0, got {total_rewards}")

    emissions_value = new_emissions * aero_price_usd
    base = emissions_value / total_rewards

    sims = [emissions_value / (total_rewards + step) for step in SIMULATION_STEPS_USD]

    return MultiplierResult(
        new_emissions=new_emissions,
        aero_price_usd=aero_price_usd,
        emissions_value=emissions_value,
        total_rewards=total_rewards,
        multiplier=base,
        sim_plus_1k=sims[0],
        sim_plus_25k=sims[1],
        sim_plus_50k=sims[2],
        sim_plus_100k=sims[3],
    )
