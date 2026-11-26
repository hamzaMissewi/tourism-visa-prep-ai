from app.db.database import Base, engine
from app.db.models import (
    User,
    ChatSession,
    ChatMessage,
    VisaPlan,
    VisaDocument,
    PendingRegistration,
    PasswordResetToken,
)

Base.metadata.create_all(bind=engine)

print("Database created successfully")
