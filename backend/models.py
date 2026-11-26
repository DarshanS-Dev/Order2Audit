"""
Order-to-Audit system: data model (SQLAlchemy 2.0, PostgreSQL).

Principles
- Stock is NEVER stored. It is SUM(ledger.qty_change) per item / finished good.
- Money and quantities use Numeric (Decimal in Python). No floats anywhere.
- Every ledger row points back to its source document (source_type + source_id).
- Posted invoices are cancelled (credit note), never deleted.
- All quantities are stored in the item's base unit (see Item.base_unit).
- Masters that have been used are retired with is_active=False, never deleted.
"""
import enum
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Numeric, String,
    Text, UniqueConstraint, Boolean, Integer, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Column type shortcuts
QTY = Numeric(14, 3)      # quantities
RATE = Numeric(14, 4)     # unit rates / costs
MONEY = Numeric(14, 2)    # rupee amounts
PCT = Numeric(5, 2)       # percentages (GST rate, tolerance)
TS = DateTime(timezone=True)  # timestamps are timezone-aware


class Base(DeclarativeBase):
    pass


def pg_enum(enum_cls: type[enum.Enum]) -> Enum:
    """Store enum *values* ('salesman'), not member names ('SALESMAN')."""
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        name=enum_cls.__name__.lower(),
    )


# ----------------------------------------------------------------- enums
class Role(str, enum.Enum):
    SALESMAN = "salesman"
    ADMIN = "admin"
    PRODUCTION = "production"
    ACCOUNTANT = "accountant"
    OWNER = "owner"


class Unit(str, enum.Enum):
    KG = "kg"
    G = "g"
    LITRE = "litre"
    ML = "ml"
    PIECE = "piece"
    PACKET = "packet"
    CARTON = "carton"


class ItemType(str, enum.Enum):
    RAW = "raw"
    PACKAGING = "packaging"


class CustomerType(str, enum.Enum):
    RETAILER = "retailer"
    DISTRIBUTOR = "distributor"


class OrderStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVOICED = "invoiced"
    DISPATCHED = "dispatched"


class InvoiceStatus(str, enum.Enum):
    POSTED = "posted"
    CANCELLED = "cancelled"


class RunStatus(str, enum.Enum):
    STARTED = "started"      # raw material issued
    COMPLETED = "completed"  # actual output recorded


class PaymentMode(str, enum.Enum):
    CASH = "cash"
    UPI = "upi"
    BANK = "bank"
    CHEQUE = "cheque"


class MovementType(str, enum.Enum):
    OPENING = "opening"
    PURCHASE = "purchase"
    PRODUCTION_ISSUE = "production_issue"      # raw/packaging consumed (negative)
    PRODUCTION_WASTAGE = "production_wastage"  # damaged/scrap (negative)
    PRODUCTION_OUTPUT = "production_output"    # finished goods made (positive)
    DISPATCH = "dispatch"                      # finished goods sold (negative)
    DISPATCH_REVERSAL = "dispatch_reversal"    # invoice cancelled (positive)
    ADJUSTMENT = "adjustment"                  # stock count correction (+/-)


# ----------------------------------------------------------------- company
class CompanySetting(Base):
    """Single-row table (id = 1): the factory's own identity, used on invoices
    and to decide CGST/SGST (same state) versus IGST (different state)."""
    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(150))
    gstin: Mapped[str] = mapped_column(String(15))
    address: Mapped[str] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(String(2))   # "33" Tamil Nadu
    state_name: Mapped[str] = mapped_column(String(50))
    invoice_prefix: Mapped[str] = mapped_column(String(10), default="INV")
    credit_note_prefix: Mapped[str] = mapped_column(String(10), default="CN")

    __table_args__ = (CheckConstraint("id = 1", name="ck_company_single_row"),)


class DocumentCounter(Base):
    """Gapless numbering per financial year. Lock the row (SELECT ... FOR UPDATE),
    increment, and use the value inside the same transaction as the invoice."""
    __tablename__ = "document_counters"

    financial_year: Mapped[str] = mapped_column(String(7), primary_key=True)  # "2026-27"
    last_invoice_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_credit_note_seq: Mapped[int] = mapped_column(Integer, default=0)


# ----------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(pg_enum(Role))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


# ----------------------------------------------------------------- masters
class Item(Base):
    """Raw materials and packaging materials (things we buy and consume)."""
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    item_type: Mapped[ItemType] = mapped_column(pg_enum(ItemType))
    base_unit: Mapped[Unit] = mapped_column(pg_enum(Unit))  # stock is kept in this unit
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    reorder_level: Mapped[Decimal | None] = mapped_column(QTY)  # stretch goal
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FinishedGood(Base):
    """Sellable product at a specific pack size, e.g. 'Murukku 200 g'."""
    __tablename__ = "finished_goods"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    pack_size_label: Mapped[str] = mapped_column(String(20))         # "200 g"
    packets_per_carton: Mapped[int | None] = mapped_column(Integer)  # 20
    hsn_code: Mapped[str] = mapped_column(String(10))
    gst_rate: Mapped[Decimal] = mapped_column(PCT, default=Decimal("5.00"))
    selling_price: Mapped[Decimal] = mapped_column(MONEY)  # price list, before tax
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    boms: Mapped[list["Bom"]] = relationship(back_populates="finished_good")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    customer_type: Mapped[CustomerType] = mapped_column(pg_enum(CustomerType))
    gstin: Mapped[str | None] = mapped_column(String(15))
    address: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str] = mapped_column(String(2))  # GST state code: "33" TN, "29" KA
    state_name: Mapped[str] = mapped_column(String(50))
    salesman_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # owning salesman
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    gstin: Mapped[str | None] = mapped_column(String(15))
    address: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(String(2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ----------------------------------------------------------------- BOM
class Bom(Base):
    """Recipe for ONE batch of ONE finished good (one BOM per pack size for now)."""
    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(primary_key=True)
    finished_good_id: Mapped[int] = mapped_column(ForeignKey("finished_goods.id"))
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    expected_yield: Mapped[Decimal] = mapped_column(QTY)  # packets per batch (200)
    tolerance_pct: Mapped[Decimal] = mapped_column(PCT, default=Decimal("3.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    finished_good: Mapped[FinishedGood] = relationship(back_populates="boms")
    lines: Mapped[list["BomLine"]] = relationship(back_populates="bom", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("finished_good_id", "version"),)


class BomLine(Base):
    __tablename__ = "bom_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty_per_batch: Mapped[Decimal] = mapped_column(QTY)
    unit: Mapped[Unit] = mapped_column(pg_enum(Unit))  # converted to item.base_unit on use

    bom: Mapped[Bom] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()

    __table_args__ = (UniqueConstraint("bom_id", "item_id"),)


# ----------------------------------------------------------------- purchases
class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    supplier_invoice_no: Mapped[str | None] = mapped_column(String(50))
    purchase_date: Mapped[date] = mapped_column(Date)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())

    lines: Mapped[list["PurchaseLine"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)    # in item.base_unit
    rate: Mapped[Decimal] = mapped_column(RATE)  # per base unit, before tax
    amount: Mapped[Decimal] = mapped_column(MONEY)
    batch_no: Mapped[str | None] = mapped_column(String(30))  # stretch
    expiry_date: Mapped[date | None] = mapped_column(Date)    # stretch

    purchase: Mapped[Purchase] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()

    __table_args__ = (CheckConstraint("qty > 0", name="ck_purchase_line_qty_pos"),)


# ----------------------------------------------------------------- production
class ProductionRun(Base):
    __tablename__ = "production_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_no: Mapped[str] = mapped_column(String(30), unique=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"))
    # Denormalised from bom.finished_good_id for easy reporting; keep in sync.
    finished_good_id: Mapped[int] = mapped_column(ForeignKey("finished_goods.id"))
    run_date: Mapped[date] = mapped_column(Date)
    batches: Mapped[Decimal] = mapped_column(QTY, default=Decimal("1"))  # batch multiplier
    status: Mapped[RunStatus] = mapped_column(pg_enum(RunStatus), default=RunStatus.STARTED)

    expected_output: Mapped[Decimal] = mapped_column(QTY)        # bom.expected_yield * batches
    actual_output: Mapped[Decimal | None] = mapped_column(QTY)   # good packets
    yield_variance_pct: Mapped[Decimal | None] = mapped_column(PCT)
    yield_flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    # Overheads for this run. Cost per packet is COMPUTED by query:
    #   (sum(run_lines.issued_qty * unit_cost) + overheads) / actual_output
    labour_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    power_gas_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    other_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())

    bom: Mapped[Bom] = relationship()
    finished_good: Mapped[FinishedGood] = relationship()
    lines: Mapped[list["RunLine"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunLine(Base):
    """
    Planned vs actual consumption of one item in one run.

    issued_qty  = TOTAL taken out of stock for the run (e.g. pouches: 194).
    wastage_qty = the part of issued_qty that was damaged/scrap (e.g. 6).
    Ledger posting: PRODUCTION_ISSUE for (issued_qty - wastage_qty)
                    PRODUCTION_WASTAGE for wastage_qty
    Variance is measured on issued_qty against planned_qty.
    """
    __tablename__ = "run_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("production_runs.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    planned_qty: Mapped[Decimal] = mapped_column(QTY)   # BOM qty * batches
    issued_qty: Mapped[Decimal] = mapped_column(QTY)    # total taken from stock
    wastage_qty: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))
    unit_cost: Mapped[Decimal] = mapped_column(RATE)    # valuation rate at issue
    variance_pct: Mapped[Decimal | None] = mapped_column(PCT)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[ProductionRun] = relationship(back_populates="lines")
    item: Mapped[Item] = relationship()

    __table_args__ = (
        UniqueConstraint("run_id", "item_id"),
        CheckConstraint("wastage_qty >= 0 AND wastage_qty <= issued_qty", name="ck_run_line_wastage"),
    )


# ----------------------------------------------------------------- sales
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_uuid: Mapped[str | None] = mapped_column(String(36), unique=True)  # offline-sync idempotency
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    salesman_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_date: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    status: Mapped[OrderStatus] = mapped_column(pg_enum(OrderStatus), default=OrderStatus.SUBMITTED)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    reject_reason: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    finished_good_id: Mapped[int] = mapped_column(ForeignKey("finished_goods.id"))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)  # admin may edit before approval

    order: Mapped[Order] = relationship(back_populates="lines")
    finished_good: Mapped[FinishedGood] = relationship()

    __table_args__ = (CheckConstraint("qty > 0", name="ck_order_line_qty_pos"),)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    financial_year: Mapped[str] = mapped_column(String(7))  # "2026-27"
    invoice_seq: Mapped[int] = mapped_column(Integer)       # from DocumentCounter
    invoice_no: Mapped[str] = mapped_column(String(30), unique=True)  # "INV/2026-27/0001"
    invoice_date: Mapped[date] = mapped_column(Date)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))

    # Snapshot of the customer's details at billing time
    customer_gstin: Mapped[str | None] = mapped_column(String(15))
    place_of_supply_code: Mapped[str] = mapped_column(String(2))
    is_igst: Mapped[bool] = mapped_column(Boolean)  # True if inter-state

    taxable_value: Mapped[Decimal] = mapped_column(MONEY)
    cgst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    sgst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    igst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    round_off: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MONEY)

    status: Mapped[InvoiceStatus] = mapped_column(pg_enum(InvoiceStatus), default=InvoiceStatus.POSTED)
    dispatched_at: Mapped[datetime | None] = mapped_column(TS)
    dispatched_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Cancellation via credit note (never delete)
    cancelled_at: Mapped[datetime | None] = mapped_column(TS)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    credit_note_no: Mapped[str | None] = mapped_column(String(30), unique=True)

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")

    __table_args__ = (UniqueConstraint("financial_year", "invoice_seq"),)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    finished_good_id: Mapped[int] = mapped_column(ForeignKey("finished_goods.id"))
    description: Mapped[str] = mapped_column(String(150))
    hsn_code: Mapped[str] = mapped_column(String(10))
    qty: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    taxable_value: Mapped[Decimal] = mapped_column(MONEY)
    gst_rate: Mapped[Decimal] = mapped_column(PCT)
    cgst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    sgst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    igst: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    finished_good: Mapped[FinishedGood] = relationship()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    payment_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    mode: Mapped[PaymentMode] = mapped_column(pg_enum(PaymentMode))
    reference: Mapped[str | None] = mapped_column(String(60))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())

    invoice: Mapped[Invoice | None] = relationship(back_populates="payments")

    __table_args__ = (CheckConstraint("amount > 0", name="ck_payment_amount_pos"),)


# ----------------------------------------------------------------- ledger
class LedgerEntry(Base):
    """
    Single source of truth for stock. Append-only.
    Balance of an item = SUM(qty_change) WHERE item_id = X
    Exactly one of item_id / finished_good_id is set.
    Posting functions must check the balance BEFORE writing a negative row.
    """
    __tablename__ = "ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    finished_good_id: Mapped[int | None] = mapped_column(ForeignKey("finished_goods.id"))
    qty_change: Mapped[Decimal] = mapped_column(QTY)          # signed, in base unit
    unit_cost: Mapped[Decimal | None] = mapped_column(RATE)   # for valuation
    movement_type: Mapped[MovementType] = mapped_column(pg_enum(MovementType))

    # Traceability: which document caused this movement
    source_type: Mapped[str] = mapped_column(String(30))  # purchase | production_run | invoice | adjustment | opening
    source_id: Mapped[int | None] = mapped_column(Integer)
    remarks: Mapped[str | None] = mapped_column(String(200))

    batch_no: Mapped[str | None] = mapped_column(String(30))  # stretch
    expiry_date: Mapped[date | None] = mapped_column(Date)    # stretch
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint(
            "(item_id IS NOT NULL) <> (finished_good_id IS NOT NULL)",
            name="ck_ledger_exactly_one_target",
        ),
        CheckConstraint("qty_change <> 0", name="ck_ledger_nonzero"),
        Index("ix_ledger_item", "item_id", "occurred_at"),
        Index("ix_ledger_fg", "finished_good_id", "occurred_at"),
        Index("ix_ledger_source", "source_type", "source_id"),
    )


# ----------------------------------------------------------------- audit trail
class AuditLog(Base):
    """Edits to invoices and production runs, with user and time."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    table_name: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))  # create | update | cancel
    old_values: Mapped[str | None] = mapped_column(Text)  # JSON
    new_values: Mapped[str | None] = mapped_column(Text)  # JSON