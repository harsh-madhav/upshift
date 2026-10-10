from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, constr, root_validator, validator

Sku = constr(regex=r"^[A-Z]{3}-\d{4}$")


class Customer(BaseModel):
    id: int
    name: str
    email: str

    @validator("email")
    def normalise_email(cls, value):
        if "@" not in value:
            raise ValueError("invalid email")
        return value.lower()

    class Config:
        orm_mode = True
        anystr_strip_whitespace = True


class LineItem(BaseModel):
    sku: Sku
    quantity: int = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)

    @property
    def total(self) -> Decimal:
        return self.quantity * self.unit_price


class Order(BaseModel):
    id: str
    customer: Customer
    items: List[LineItem]
    coupon: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("coupon")
    def upper_coupon(cls, value):
        return value.upper() if value else value

    @root_validator
    def needs_items(cls, values):
        if not values.get("items"):
            raise ValueError("an order needs at least one item")
        return values

    class Config:
        allow_population_by_field_name = True
        fields = {"id": {"alias": "orderId"}}
        schema_extra = {
            "example": {
                "orderId": "ord_1",
                "customer": {"id": 1, "name": "Ada", "email": "ada@example.com"},
                "items": [{"sku": "ABC-0001", "quantity": 2, "unit_price": "9.99"}],
            }
        }
