import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import pandas as pd

DATABASE_URI = 'sqlite:///finances.db'

engine = create_engine(DATABASE_URI)
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

# Define database models (copying from app.py for now, might need to refactor later)
class AccountBalance(Base):
    __tablename__ = 'account_balances'
    id = Column(Integer, primary_key=True)
    account_name = Column(String(120), unique=False, nullable=False)
    balance = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False)

    def __init__(self, account_name=None, balance=None, timestamp=None):
        self.account_name = account_name
        self.balance = balance
        self.timestamp = timestamp

    def __repr__(abbr):
        return '<AccountBalance %r>' % (abbr)

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

# Helper function to process the uploaded Excel file
def process_excel_data(filepath):
    try:
        xls = pd.ExcelFile(filepath, engine='openpyxl')
        # Read the single worksheet, skipping the first 3 rows for headers
        df = xls.parse('SWASTIK_9469790', skiprows=3)

        # Print column names for debugging (optional, can remove after confirming)
        # print("DataFrame columns:", df.columns.tolist())

        # Convert 'Trade Date' column to datetime objects
        df['Trade Date'] = pd.to_datetime(df['Trade Date'])

        # Process Mutual Fund Transactions
        for index, row in df.iterrows():
            # Determine transaction type and units based on 'Buy units' and 'Sell units'
            buy_units = row.get('Buy units', 0)
            sell_units = row.get('Sell units', 0)
            transaction_type = None
            units = 0

            if buy_units > 0:
                transaction_type = 'Buy'
                units = buy_units
            elif sell_units > 0:
                transaction_type = 'Sell'
                units = sell_units

            # Determine amount based on 'Cash inflow' and 'Cash outflow'
            cash_inflow = row.get('Cash inflow', 0)
            cash_outflow = row.get('Cash outflow', 0)
            amount = 0

            if cash_inflow > 0:
                amount = cash_inflow
            elif cash_outflow > 0:
                amount = cash_outflow

            # Get fund name from the correct column header, provide a default if missing
            fund_name = row.get('Investment name') # Use the correct column name
            if fund_name is None or (isinstance(fund_name, str) and not fund_name.strip()):
                 fund_name = 'Unknown Fund'

            # Map column names to database model fields
            transaction_entry = MutualFundTransaction(
                fund_name=fund_name, # Use the handled fund_name
                transaction_type=transaction_type,
                amount=amount,
                units=units,
                nav=row.get('NAV', 0), # Assuming 'NAV' column exists or default to 0
                timestamp=row.get('Trade Date') # Use the converted datetime object
            )
            db_session.add(transaction_entry)

        db_session.commit()
        return True
    except Exception as e:
        db_session.rollback()
        print(f"Error processing Excel file: {e}")
        return False

if __name__ == '__main__':
    # Process the specific file requested by the user
    file_to_process = 'uploads/TXReports-family-Id9469790-25-05-16_03_18_07.xlsx'
    if os.path.exists(file_to_process):
        print(f"Processing file: {file_to_process}")
        if process_excel_data(file_to_process):
            print("Data processing successful.")
        else:
            print("Data processing failed.")
    else:
        print(f"File not found: {file_to_process}")
