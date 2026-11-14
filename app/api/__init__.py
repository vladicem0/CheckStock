"""Request-local sessions for synchronous API handlers.

The dependency opens a fresh session for each request. Handlers own the command
transaction or establish a report snapshot before their first SQL statement.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session

SessionDep = Annotated[Session, Depends(get_session)]
