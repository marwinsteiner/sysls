"""Position reconciliation between OMS and venue.

Compares internal OMS position state against venue-reported positions to
detect drift. Runs periodically or on-demand to ensure the OMS accurately
reflects the venue's actual state.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from sysls.core.types import Instrument  # noqa: TC001 — needed at runtime for Pydantic models

if TYPE_CHECKING:
    from sysls.execution.oms import OrderManagementSystem
    from sysls.execution.venues.base import VenueAdapter


class PositionDiscrepancy(BaseModel, frozen=True):
    """A mismatch between OMS and venue position state.

    Attributes:
        instrument: The instrument with the mismatch.
        oms_quantity: Position quantity per the OMS.
        venue_quantity: Position quantity per the venue.
        difference: venue_quantity - oms_quantity.
    """

    instrument: Instrument
    oms_quantity: Decimal
    venue_quantity: Decimal
    difference: Decimal


class ReconciliationReport(BaseModel, frozen=True):
    """Result of a reconciliation run.

    Attributes:
        venue_name: Name of the venue reconciled.
        is_consistent: True if OMS and venue agree on all positions.
        discrepancies: List of position mismatches.
        oms_only: Instruments with OMS positions but no venue position.
        venue_only: Instruments with venue positions but no OMS position.
    """

    venue_name: str
    is_consistent: bool
    discrepancies: list[PositionDiscrepancy] = []
    oms_only: list[Instrument] = []
    venue_only: list[Instrument] = []


class PositionReconciler:
    """Reconciles OMS positions against venue-reported positions.

    Runs periodically or on-demand to detect drift between internal
    position tracking and the venue's actual state.
    """

    def __init__(self) -> None:
        self._logger = structlog.get_logger(__name__)

    async def reconcile(
        self,
        oms: OrderManagementSystem,
        venue: VenueAdapter,
    ) -> ReconciliationReport:
        """Compare OMS positions with venue positions.

        Fetches positions from both the OMS and the venue, then compares
        them instrument by instrument. Reports discrepancies (quantity
        mismatches), OMS-only positions, and venue-only positions.

        Args:
            oms: The Order Management System to reconcile.
            venue: The venue adapter to compare against.

        Returns:
            ReconciliationReport with comparison results.
        """
        # Get OMS positions (filter out zero-quantity positions).
        oms_positions = {
            instrument: pos.quantity
            for instrument, pos in oms.get_all_positions().items()
            if pos.quantity != Decimal("0")
        }

        # Get venue positions (filter out zero-quantity positions).
        venue_positions = {
            instrument: qty
            for instrument, qty in (await venue.get_positions()).items()
            if qty != Decimal("0")
        }

        oms_instruments = set(oms_positions.keys())
        venue_instruments = set(venue_positions.keys())

        # Instruments present in both.
        common = oms_instruments & venue_instruments

        # Instruments present only in OMS or only at venue.
        oms_only_instruments = sorted(oms_instruments - venue_instruments, key=str)
        venue_only_instruments = sorted(venue_instruments - oms_instruments, key=str)

        # Check for quantity mismatches in common instruments.
        discrepancies: list[PositionDiscrepancy] = []
        for instrument in sorted(common, key=str):
            oms_qty = oms_positions[instrument]
            venue_qty = venue_positions[instrument]
            if oms_qty != venue_qty:
                discrepancies.append(
                    PositionDiscrepancy(
                        instrument=instrument,
                        oms_quantity=oms_qty,
                        venue_quantity=venue_qty,
                        difference=venue_qty - oms_qty,
                    )
                )

        is_consistent = (
            len(discrepancies) == 0
            and len(oms_only_instruments) == 0
            and len(venue_only_instruments) == 0
        )

        report = ReconciliationReport(
            venue_name=venue.name,
            is_consistent=is_consistent,
            discrepancies=discrepancies,
            oms_only=list(oms_only_instruments),
            venue_only=list(venue_only_instruments),
        )

        if is_consistent:
            self._logger.info(
                "reconciliation_consistent",
                venue=venue.name,
            )
        else:
            self._logger.warning(
                "reconciliation_discrepancies_found",
                venue=venue.name,
                num_discrepancies=len(discrepancies),
                num_oms_only=len(oms_only_instruments),
                num_venue_only=len(venue_only_instruments),
            )

        return report
