"""DiscoveredFile model for virtual file explorer."""
import re
from datetime import datetime
import uuid
from typing import Optional, TYPE_CHECKING
from sqlalchemy import String, DateTime, JSON, Text, Integer, ForeignKey, BigInteger, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.host import Host
    from app.models.execution import Execution


class DiscoveredFile(Base):
    """
    File discovered through SMB/NFS enumeration or similar tools.
    
    Represents files in a virtual file explorer that can be searched,
    downloaded, or even uploaded to (if permissions allow).
    
    Attributes:
        id: Primary key
        host_id: Foreign key to host where file was found
        execution_id: Foreign key to execution that discovered it
        share_name: Name of the share (e.g., "C$", "Documents")
        path: Full path within the share
        name: Filename
        file_type: Type (file, directory, link)
        size: File size in bytes
        permissions: Parsed permissions info
        is_readable: Whether file can be read
        is_writable: Whether file can be written
    """
    __tablename__ = "discovered_files"
    __table_args__ = (
        Index(
            "ix_discovered_files_host_share_path",
            "host_id",
            "share_name",
            "path",
        ),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    execution_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Location
    share_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # File info
    file_type: Mapped[str] = mapped_column(String(50), default="file", nullable=False)
    # Types: "file", "directory", "link", "special"
    
    size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    
    # Permissions
    permissions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "raw": "drwxr-xr-x",
    #     "owner": "Administrator",
    #     "group": "Domain Users"
    # }
    
    is_readable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_writable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_executable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Interesting file flags
    is_interesting: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interest_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Reasons: "password_file", "config_file", "backup", "sensitive_data"
    
    # Content preview (for small text files)
    content_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Download info
    local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Metadata
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Can include: modified_time, accessed_time, created_time, attributes
    
    # Timestamps
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    
    # Relationships
    host: Mapped["Host"] = relationship("Host", back_populates="discovered_files")
    
    execution: Mapped[Optional["Execution"]] = relationship(
        "Execution",
        back_populates="discovered_files"
    )
    
    def __repr__(self) -> str:
        return f"<DiscoveredFile(id={self.id}, path='{self.path}/{self.name}')>"
    
    @property
    def full_path(self) -> str:
        """Return full UNC-style path."""
        raw = f"//{self.share_name}/{self.path}/{self.name}"
        return re.sub(r'/+', '/', raw)
    
    @property
    def extension(self) -> Optional[str]:
        """Return file extension if present."""
        if "." in self.name and self.file_type == "file":
            return self.name.rsplit(".", 1)[-1].lower()
        return None
    
    @classmethod
    def is_interesting_file(cls, filename: str) -> tuple[bool, Optional[str]]:
        """Check if a filename is potentially interesting."""
        filename_lower = filename.lower()
        
        # Password files
        if any(kw in filename_lower for kw in ["password", "passwd", "credentials", "secret", "key"]):
            return True, "password_file"
        
        # Config files
        if any(filename_lower.endswith(ext) for ext in [".conf", ".config", ".ini", ".cfg", ".xml", ".yaml", ".yml"]):
            return True, "config_file"
        
        # Backup files
        if any(filename_lower.endswith(ext) for ext in [".bak", ".backup", ".old", ".orig"]):
            return True, "backup"
        
        # Database files
        if any(filename_lower.endswith(ext) for ext in [".db", ".sqlite", ".sql", ".mdb"]):
            return True, "database"
        
        # SSH/crypto keys
        if any(kw in filename_lower for kw in ["id_rsa", "id_dsa", "id_ecdsa", ".pem", ".key", ".crt", ".cer"]):
            return True, "crypto_key"
        
        # SAM/NTDS files
        if filename_lower in ["sam", "system", "ntds.dit", "security"]:
            return True, "windows_secrets"
        
        return False, None
