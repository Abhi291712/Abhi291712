"""Database access for calls.

Repositories hide SQLAlchemy queries behind small, intention-revealing methods. Services call
``calls.get_by_external_id(...)`` instead of building queries themselves, which keeps business
logic readable and means a query can be optimised in one place.

Repositories ``flush`` (send SQL to the database inside the current transaction) but never
``commit``. The service decides when a unit of work is complete and commits it.
"""

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.call import Call


class CallRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, call: Call) -> Call:
        self.session.add(call)
        self.session.flush()  # Assigns defaults and surfaces constraint violations right away.
        return call

    def get(self, call_id: str) -> Call | None:
        return self.session.get(Call, call_id)

    def get_by_external_id(self, external_call_id: str) -> Call | None:
        return self.session.scalar(select(Call).where(Call.external_call_id == external_call_id))

    def get_by_idempotency_key(self, key: str) -> Call | None:
        return self.session.scalar(select(Call).where(Call.idempotency_key == key))

    def list_page(
        self,
        *,
        limit: int,
        status: str | None = None,
        agent_id: str | None = None,
        after: tuple[datetime, str] | None = None,
    ) -> list[Call]:
        """Return up to `limit` calls, newest first, starting after the given cursor position.

        This is keyset (cursor) pagination: instead of OFFSET, which gets slower and can skip or
        repeat rows when data changes, we remember the (created_at, id) of the last row served
        and ask for rows strictly "older" than it. `id` breaks ties between equal timestamps.
        """
        query = select(Call)
        if status:
            query = query.where(Call.status == status)
        if agent_id:
            query = query.where(Call.agent_id == agent_id)
        if after:
            created_at, last_id = after
            query = query.where(
                or_(
                    Call.created_at < created_at,
                    and_(Call.created_at == created_at, Call.id < last_id),
                )
            )
        query = query.order_by(Call.created_at.desc(), Call.id.desc()).limit(limit)
        return list(self.session.scalars(query))
