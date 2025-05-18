from sqlalchemy import Column, Integer, String, Float, DateTime
#from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class AccountBalance(Base):
    __tablename__ = 'account_balances'
    id = Column(Integer, primary_key=True)
    bank = Column(String(120), unique=False, nullable=True)
    date = Column(DateTime, nullable=False) # Corresponds to 'Date'
    narration = Column(String(255), unique=False, nullable=True)
    chq_ref_no = Column(String(120), unique=False, nullable=True)
    withdrawal_amt = Column(Float, nullable=True)
    deposit_amt = Column(Float, nullable=True)
    closing_balance = Column(Float, nullable=False) # Corresponds to 'Closing Balance'

    def __init__(self, bank=None, date=None, narration=None, chq_ref_no=None, withdrawal_amt=None, deposit_amt=None, closing_balance=None):
        self.bank = bank
        self.date = date
        self.narration = narration
        self.chq_ref_no = chq_ref_no
        self.withdrawal_amt = withdrawal_amt
        self.deposit_amt = deposit_amt
        self.closing_balance = closing_balance

    def __repr__(self):
        return '<AccountBalance %r>' % (self.bank)

class Fund(Base):
    __tablename__ = 'funds'
    id = Column(Integer, primary_key=True)
    fund_name = Column(String(120), unique=True, nullable=False)
    fund_code = Column(String(20), unique=True, nullable=False)
    current_nav = Column(Float, nullable=True) # Store current NAV here
    last_updated = Column(DateTime, nullable=True) # Add last updated timestamp

    def __init__(self, fund_name=None, fund_code=None, current_nav=None, last_updated=None):
        self.fund_name = fund_name
        self.fund_code = fund_code
        self.current_nav = current_nav
        self.last_updated = last_updated

    def __repr__(self):
        return '<Fund %r>' % (self.fund_name)

class MutualFundTransaction(Base):
    __tablename__ = 'mutual_fund_transactions'
    id = Column(Integer, primary_key=True)
    fund_name = Column(String(120), unique=False, nullable=False)
    transaction_type = Column(String(50), nullable=False) # e.g., Buy, Sell
    amount = Column(Float, nullable=False)
    units = Column(Float, nullable=False)
    nav = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False)

    def __init__(self, fund_name=None, transaction_type=None, amount=None, units=None, nav=None, timestamp=None):
        self.fund_name = fund_name
        self.transaction_type = transaction_type
        self.amount = amount
        self.units = units
        self.nav = nav
        self.timestamp = timestamp

    def __repr__(self):
        return '<MutualFundTransaction %r>' % (self.fund_name)

class FixedDeposit(Base):
    __tablename__ = 'fixed_deposits'
    id = Column(Integer, primary_key=True)
    bank = Column(String(120), nullable=False)
    amount = Column(Float, nullable=False)
    interest_rate = Column(Float, nullable=False)
    start_date = Column(DateTime, nullable=False)
    maturity_date = Column(DateTime, nullable=False)
    total_interest_earned = Column(Float, nullable=True, default=0.0) # Field for tracking interest
    status = Column(String(50), nullable=False, default='open') # New field for status (open, closed, matured)
    closure_date = Column(DateTime, nullable=True) # New field for closure/maturity date

    def __init__(self, bank=None, amount=None, interest_rate=None, start_date=None, maturity_date=None, total_interest_earned=0.0, status='open', closure_date=None):
        self.bank = bank
        self.amount = amount
        self.interest_rate = interest_rate
        self.start_date = start_date
        self.maturity_date = maturity_date
        self.total_interest_earned = total_interest_earned
        self.status = status
        self.closure_date = closure_date

    def __repr__(self):
        return '<FixedDeposit %r>' % (self.bank)
