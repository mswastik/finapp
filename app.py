import os
from flask import Flask, request, redirect, url_for, render_template
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import pandas as pd
import sys
import json
import requests
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from pyxirr import xirr
import datetime
#import io
#import csv

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx'}
DATABASE_URI = 'sqlite:///finances.db'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

def load_fund_codes(cache_file='fund_mapping_cache.json'):
    fund_code_mapping = {}

    try:
        with open(cache_file, 'r') as f:
            fund_code_mapping = json.load(f)
            print(f"Loaded {len(fund_code_mapping)} fund mappings from cache.")
            return fund_code_mapping
    except (FileNotFoundError, json.JSONDecodeError):
        print("No valid cache found. Fetching from API...")

    try:
        # Fetch the list of all mutual funds
        response = requests.get("https://api.mfapi.in/mf")

        if response.status_code == 200:
            funds_data = response.json()
            total_funds = len(funds_data)
            print(f"Found {total_funds} funds. Processing...")

            # Process each fund - get scheme code and name
            for i, fund in enumerate(funds_data):
                if i % 100 == 0:
                    print(f"Processed {i}/{total_funds} funds...")

                scheme_code = fund.get("schemeCode")
                scheme_name = fund.get("schemeName", "").strip().lower()

                if scheme_code and scheme_name:
                    fund_code_mapping[scheme_name] = str(scheme_code)

                    # Also add a version without the dash format to improve matching
                    if " - " in scheme_name:
                        no_dash_name = scheme_name.replace(" - ", " ")
                        fund_code_mapping[no_dash_name] = str(scheme_code)

            # Cache the results
            try:
                with open(cache_file, 'w') as f:
                    json.dump(fund_code_mapping, f)
                print(f"Cached {len(fund_code_mapping)} fund mappings.")
            except Exception as e:
                print(f"Warning: Could not save cache: {e}")

        else:
            print(f"API request failed with status code: {response.status_code}")

    except Exception as e:
        print(f"Error fetching fund codes from API: {e}")

    return fund_code_mapping

def fetch_current_nav(fund_code):
    """Fetches the current NAV for a given fund code."""
    if not fund_code:
        return None

    try:
        response = requests.get(f"https://api.mfapi.in/mf/{fund_code}")
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and data['data']:
                # The latest NAV is the first entry in the 'data' list
                latest_data = data['data'][0]
                return float(latest_data.get('nav'))
        else:
            print(f"Error fetching NAV for fund code {fund_code}: Status code {response.status_code}")
    except Exception as e:
        print(f"Error fetching NAV for fund code {fund_code}: {e}")

    return None


def init_db():
    Base.metadata.create_all(bind=engine)

# Define database models
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

    def __repr__(self):
        return '<AccountBalance %r>' % (self.account_name)

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

# Helper function to process the uploaded Excel files
def process_excel_data(mutual_funds_filepath): # Only accept mutual funds file path
    try:
        try:
            # Read Mutual Fund Transactions
            mutual_funds_xls = pd.ExcelFile(mutual_funds_filepath, engine='openpyxl')
            mutual_funds_df = mutual_funds_xls.parse('SWASTIK_9469790', skiprows=3) # Read from the specified sheet name and skip header rows

            # Convert 'Trade Date' to datetime objects
            mutual_funds_df['Trade Date'] = pd.to_datetime(mutual_funds_df['Trade Date'])

        except Exception as e:
            print(f"Error reading Excel file: {e}")
            return False

        # Account Balances processing (commented out as per user request)
        # try:
        #     account_balances_xls = pd.ExcelFile(account_balances_filepath, engine='openpyxl')
        #     account_balances_df = account_balances_xls.parse('Account Balances')
        #     for index, row in account_balances_df.iterrows():
        #         balance_entry = AccountBalance(
        #             account_name=row['Account Name'],
        #             balance=row['Balance'],
        #             timestamp=row['Timestamp'] # Assuming timestamp is in a suitable format
        #         )
        #         db_session.add(balance_entry)
        # except Exception as e:
        #     print(f"Error processing Account Balances file: {e}")
        #     # Decide how to handle this error - continue with mutual funds or return False?
        #     # For now, let's continue with mutual funds processing
        #     pass


        # Load fund codes mapping for processing
        fund_code_mapping = load_fund_codes()

        # Load fund codes mapping for processing
        fund_code_mapping = load_fund_codes()

        # Get unique fund names and find their codes once, and add/update Fund table
        unique_fund_names = mutual_funds_df['Investment name'].unique()

        for fund_name in unique_fund_names:
            fund_code = fund_code_mapping.get(fund_name.lower()) # Get fund code from mapping (case-insensitive)

            if not fund_code:
                # If direct match not found, try fuzzy matching
                best_match, score = process.extractOne(fund_name.lower(), fund_code_mapping.keys())
                if score > 80: # Use a threshold, e.g., 80
                    fund_code = fund_code_mapping.get(best_match)
                    print(f"Fuzzy matched '{fund_name}' to '{best_match}' with score {score}. Using code {fund_code}")
                else:
                    print(f"Fund code not found for '{fund_name}' and no good fuzzy match found (best match: '{best_match}', score: {score})")

            # Add or update the Fund table
            fund_entry = db_session.query(Fund).filter_by(fund_name=fund_name).first()
            if fund_entry:
                if fund_code and not fund_entry.fund_code: # Update fund_code if it was missing
                    fund_entry.fund_code = fund_code
            else:
                fund_entry = Fund(fund_name=fund_name, fund_code=fund_code)
                db_session.add(fund_entry)

            # Fetch and update current_nav if fund_code is available
            if fund_entry.fund_code:
                current_nav = fetch_current_nav(fund_entry.fund_code)
                if current_nav is not None:
                    fund_entry.current_nav = current_nav
                    fund_entry.last_updated = datetime.datetime.now()
                    print(f"Updated NAV for {fund_name} ({fund_entry.fund_code}): {current_nav}")
                else:
                    print(f"Could not fetch NAV for {fund_name} ({fund_entry.fund_code})")


        db_session.commit() # Commit fund updates

        # Process Mutual Fund Transactions
        for index, row in mutual_funds_df.iterrows():
            # print(row) # Uncomment for debugging row data
            fund_name = row['Investment name']

            transaction_type = None
            amount = 0.0
            units = 0.0
            nav = 0.0 # NAV is missing, setting to 0 for now

            if row.get('Buy units', 0) > 0:
                transaction_type = 'Buy'
                units = row['Buy units']
                amount = row.get('Cash inflow', 0)
            elif row.get('Sell units', 0) > 0:
                transaction_type = 'Sell'
                units = row['Sell units']
                amount = row.get('Cash outflow', 0)
            elif row.get('Dividend reinvested units', 0) > 0:
                 transaction_type = 'Buy' # Reinvestment is a form of buying units
                 units = row['Dividend reinvested units']
                 amount = row.get('Dividend Amount', 0)

            calculated_nav = 0.0
            if units != 0:
                # Use absolute value of amount for NAV calculation for sell transactions
                nav_amount = abs(amount) if transaction_type == 'Sell' else amount
                calculated_nav = nav_amount / units

            if transaction_type: # Only process if a transaction type is determined
                transaction_entry = MutualFundTransaction(
                    fund_name=fund_name,
                    transaction_type=transaction_type,
                    amount=amount,
                    units=units,
                    nav=calculated_nav, # Use calculated NAV
                    timestamp=row['Trade Date']
                )
                db_session.add(transaction_entry)

        db_session.commit() # Commit transaction updates
        return True
    except Exception as e:
        db_session.rollback()
        print(f"Error processing Excel file: {e}")
        return False

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'mutual_funds_file' not in request.files or 'account_balances_file' not in request.files:
            return "Missing one or both files", 400

        mutual_funds_file = request.files['mutual_funds_file']
        account_balances_file = request.files['account_balances_file']

        if mutual_funds_file.filename == '' and account_balances_file.filename == '':
            return "One or both files have no selected file", 400

        if mutual_funds_file and allowed_file(mutual_funds_file.filename): # and account_balances_file and allowed_file(account_balances_file.filename):
            mutual_funds_filename = secure_filename(mutual_funds_file.filename)
            #account_balances_filename = secure_filename(account_balances_file.filename)

            mutual_funds_filepath = os.path.join(app.config['UPLOAD_FOLDER'], mutual_funds_filename)
            #account_balances_filepath = os.path.join(app.config['UPLOAD_FOLDER'], account_balances_filename)

            mutual_funds_file.save(mutual_funds_filepath)
            #account_balances_file.save(account_balances_filepath)

            if process_excel_data(mutual_funds_filepath): #, account_balances_filepath):
                return 'Files successfully uploaded and data processed'
            else:
                return 'Files uploaded but data processing failed'
        else:
            return 'Invalid file type for one or both files'
    return render_template('index.html')

@app.route('/balances')
def show_balances():
    account_balances = AccountBalance.query.order_by(AccountBalance.timestamp.desc()).all()
    return render_template('balances.html', account_balances=account_balances)

@app.route('/transactions')
def show_transactions():
    transactions = MutualFundTransaction.query.order_by(MutualFundTransaction.timestamp.desc()).all()
    return render_template('transactions.html', transactions=transactions)

@app.route('/performance')
def show_performance():
    fund_performance = {}
    transactions = MutualFundTransaction.query.order_by(MutualFundTransaction.timestamp).all()

    # Fetch all funds to get fund_code and current_nav
    funds = db_session.query(Fund).all()
    fund_info = {fund.fund_name: {'fund_code': fund.fund_code, 'current_nav': fund.current_nav} for fund in funds}

    for transaction in transactions:
        fund_name = transaction.fund_name

        if fund_name not in fund_performance:
            fund_performance[fund_name] = {
                'total_invested': 0,
                'total_units': 0,
                'realized_gains': 0.0,
                'unrealized_gains': 0.0,
                'xirr_cash_flows': [], # For XIRR calculation [(amount, date)]
                'cost_basis': 0.0, # For average cost basis tracking
                'transactions': [],
                'fund_code': fund_info.get(fund_name, {}).get('fund_code'), # Get fund code from fund_info
                'current_nav': fund_info.get(fund_name, {}).get('current_nav', 0.0) # Get current_nav from fund_info
            }

        fund_data = fund_performance[fund_name]
        fund_data['transactions'].append(transaction)

        # For XIRR calculation, amount is negative for buys, positive for sells
        xirr_amount = -abs(transaction.amount) if transaction.transaction_type.lower() == 'buy' else abs(transaction.amount)
        fund_data['xirr_cash_flows'].append((xirr_amount, transaction.timestamp))


        if transaction.transaction_type.lower() == 'buy':
            fund_data['total_invested'] += transaction.amount
            fund_data['total_units'] += transaction.units
            # Update average cost basis
            fund_data['cost_basis'] += transaction.amount
        elif transaction.transaction_type.lower() == 'sell':
            # Calculate realized gains using average cost basis
            if fund_data['total_units'] > 0:
                average_cost_per_unit = fund_data['cost_basis'] / fund_data['total_units']
                realized_gain = (transaction.nav - average_cost_per_unit) * transaction.units
                fund_data['realized_gains'] += realized_gain # Take absolute value of realized gain

            # Update total_units for sell transactions
            fund_data['total_units'] -= transaction.units
            # Update cost basis after selling
            fund_data['cost_basis'] -= average_cost_per_unit * transaction.units


    # Calculate Unrealized Gains and XIRR
    import numpy_financial as npf
    import datetime
    import requests

    for fund_name, fund_data in fund_performance.items():
        current_nav = fund_data['current_nav'] # Use current_nav from fund_info
        # Calculate Unrealized Gains and XIRR
        if fund_data['total_units'] > 0 and current_nav > 0:
            current_value = fund_data['total_units'] * current_nav
            # Unrealized gain is current value minus the remaining cost basis
            remaining_cost_basis = fund_data['cost_basis']
            fund_data['unrealized_gains'] = current_value - remaining_cost_basis
        else:
             fund_data['unrealized_gains'] = 0.0 # No units or current NAV, no unrealized gain

        # Calculate XIRR
        if len(fund_data['xirr_cash_flows']) > 1 and current_nav > 0:
            # Add the current value as a final cash flow at today's date for XIRR
            today = datetime.date.today()
            final_cash_flow_amount = fund_data['total_units'] * current_nav
            xirr_values = [cf[0] for cf in fund_data['xirr_cash_flows']] + [final_cash_flow_amount]
            xirr_dates = [cf[1] for cf in fund_data['xirr_cash_flows']] + [today]

            try:
                # Convert datetime objects to date objects for xirr
                xirr_dates = [d.date() if isinstance(d, datetime.datetime) else d for d in xirr_dates]
                fund_data['xirr'] = xirr(xirr_dates,xirr_values)
            except Exception as e:
                print(f"  Error calculating overall XIRR: {e}")
                fund_data['xirr'] = 0.0 # Handle cases where XIRR cannot be calculated (e.g., no cash flows)
        else:
            print(f"XIRR not calculated for {fund_name}: Insufficient cash flows or current NAV <= 0")
            fund_data['xirr'] = 0.0 # Not enough cash flows or current NAV to calculate XIRR

        # Calculate Unrealized Gains and XIRR
        if fund_data['total_units'] > 0 and current_nav > 0:
            current_value = fund_data['total_units'] * current_nav
            # Unrealized gain is current value minus the remaining cost basis
            remaining_cost_basis = fund_data['cost_basis']
            fund_data['unrealized_gains'] = current_value - remaining_cost_basis
        else:
             fund_data['unrealized_gains'] = 0.0 # No units or current NAV, no unrealized gain

        # Calculate XIRR
        if len(fund_data['xirr_cash_flows']) > 1 and current_nav > 0:
            # Add the current value as a final cash flow at today's date for XIRR
            today = datetime.date.today()
            final_cash_flow_amount = fund_data['total_units'] * current_nav
            xirr_values = [cf[0] for cf in fund_data['xirr_cash_flows']] + [final_cash_flow_amount]
            xirr_dates = [cf[1] for cf in fund_data['xirr_cash_flows']] + [today]

            try:
                # Convert datetime objects to date objects for xirr
                xirr_dates = [d.date() if isinstance(d, datetime.datetime) else d for d in xirr_dates]
                fund_data['xirr'] = xirr(xirr_dates,xirr_values)
            except Exception as e:
                print(f"  Error calculating overall XIRR: {e}")
                fund_data['xirr'] = 0.0 # Handle cases where XIRR cannot be calculated (e.g., no cash flows)
        else:
            print(f"XIRR not calculated for {fund_name}: Insufficient cash flows or current NAV <= 0")
            fund_data['xirr'] = 0.0 # Not enough cash flows or current NAV to calculate XIRR

    # Calculate total realized and unrealized gains
    total_realized_gains = sum(fund['realized_gains'] for fund in fund_performance.values())
    total_unrealized_gains = sum(fund['unrealized_gains'] for fund in fund_performance.values())

    # Calculate overall XIRR
    overall_xirr_cash_flows = []
    for fund_data in fund_performance.values():
        overall_xirr_cash_flows.extend(fund_data['xirr_cash_flows'])

    overall_xirr = 0.0
    if len(overall_xirr_cash_flows) > 1:
        # Add the current total value as a final cash flow at today's date for overall XIRR
        today = datetime.date.today()
        total_current_value = sum(fund['total_units'] * fund['current_nav'] for fund in fund_performance.values() if fund['current_nav'] > 0)
        overall_xirr_values = [cf[0] for cf in overall_xirr_cash_flows] + [total_current_value]
        overall_xirr_dates = [cf[1] for cf in overall_xirr_cash_flows] + [today]

        try:
            # Convert datetime objects to date objects for xirr
            overall_xirr_dates = [d.date() if isinstance(d, datetime.datetime) else d for d in overall_xirr_dates]
            overall_xirr = xirr(overall_xirr_dates, overall_xirr_values)
        except Exception as e:
            print(f"  Error calculating overall XIRR: {e}")
            overall_xirr = 0.0

    # Calculate total realized and unrealized gains
    total_realized_gains = sum(fund['realized_gains'] for fund in fund_performance.values())
    total_unrealized_gains = sum(fund['unrealized_gains'] for fund in fund_performance.values())

    # Calculate overall XIRR
    overall_xirr_cash_flows = []
    for fund_data in fund_performance.values():
        overall_xirr_cash_flows.extend(fund_data['xirr_cash_flows'])

    overall_xirr = 0.0
    if len(overall_xirr_cash_flows) > 1:
        # Add the current total value as a final cash flow at today's date for overall XIRR
        today = datetime.date.today()
        total_current_value = sum(fund['total_units'] * fund['current_nav'] for fund in fund_performance.values() if fund['current_nav'] > 0)
        overall_xirr_values = [cf[0] for cf in overall_xirr_cash_flows] + [total_current_value]
        overall_xirr_dates = [cf[1] for cf in overall_xirr_cash_flows] + [today]

        try:
            # Convert datetime objects to date objects for xirr
            overall_xirr_dates = [d.date() if isinstance(d, datetime.datetime) else d for d in overall_xirr_dates]
            overall_xirr = xirr(overall_xirr_dates, overall_xirr_values)
        except Exception as e:
            print(f"  Error calculating overall XIRR: {e}")
            overall_xirr = 0.0

    # --- Chart Data Preparation ---
    portfolio_history = {}
    fund_history = {fund_name: {} for fund_name in fund_performance.keys()}

    all_dates = sorted(list(set([t.timestamp.date() for t in transactions])))
    if not all_dates:
        all_dates = [datetime.date.today()] # Ensure at least today's date if no transactions

    # Initialize holdings and cost basis
    current_holdings = {fund_name: 0.0 for fund_name in fund_performance.keys()}
    current_cost_basis = {fund_name: 0.0 for fund_name in fund_performance.keys()}

    # Iterate through dates and calculate portfolio value
    for date in all_dates:
        # Process transactions up to this date
        transactions_on_date = [t for t in transactions if t.timestamp.date() == date]
        for transaction in transactions_on_date:
            fund_name = transaction.fund_name
            if transaction.transaction_type.lower() == 'buy':
                current_holdings[fund_name] += transaction.units
                current_cost_basis[fund_name] += transaction.amount
            elif transaction.transaction_type.lower() == 'sell':
                 # Adjust cost basis for sells based on average cost
                 if fund_performance[fund_name]['total_units'] > 0: # Use total units from initial calculation
                     average_cost_per_unit = fund_performance[fund_name]['cost_basis'] / fund_performance[fund_name]['total_units']
                     current_cost_basis[fund_name] -= average_cost_per_unit * transaction.units
                 current_holdings[fund_name] -= transaction.units


        # Fetch NAVs for this date (simplified: using latest available NAV)
        # A more accurate approach would fetch historical NAVs for each date
        current_navs = {fund_name: fund_performance[fund_name]['current_nav'] for fund_name in fund_performance.keys()} # Using latest NAV for simplicity

        # Calculate portfolio value on this date
        portfolio_value_on_date = 0
        for fund_name, units in current_holdings.items():
            if fund_name in current_navs and current_navs[fund_name] > 0:
                 fund_value = units * current_navs[fund_name]
                 portfolio_value_on_date += fund_value
                 fund_history[fund_name][date.isoformat()] = fund_value # Store fund value history

        portfolio_history[date.isoformat()] = portfolio_value_on_date # Store total portfolio value history

    # Ensure today's value is included if not already
    today_str = datetime.date.today().isoformat()
    if today_str not in portfolio_history:
         total_current_value = sum(fund['total_units'] * fund['current_nav'] for fund in fund_performance.values() if fund['current_nav'] > 0)
         portfolio_history[today_str] = total_current_value
         for fund_name, fund_data in fund_performance.items():
              if fund_data['current_nav'] > 0:
                   fund_history[fund_name][today_str] = fund_data['total_units'] * fund_data['current_nav']


    # Sort history by date
    sorted_portfolio_history = sorted(portfolio_history.items())
    sorted_fund_history = {fund_name: sorted(history.items()) for fund_name, history in fund_history.items()}


    return render_template('performance.html',
                           fund_performance=fund_performance,
                           total_realized_gains=total_realized_gains,
                           total_unrealized_gains=total_unrealized_gains,
                           overall_xirr=overall_xirr,
                           portfolio_history=sorted_portfolio_history,
                           fund_history=sorted_fund_history)

@app.route('/update_database', methods=['GET'])
def update_database_form():
    return render_template('update_database.html')

@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    try:
        fund_name = request.form['fund_name']
        transaction_type = request.form['transaction_type']
        amount = float(request.form['amount'])
        units = float(request.form['units'])
        nav = float(request.form['nav'])
        timestamp_str = request.form['timestamp']
        timestamp = datetime.datetime.fromisoformat(timestamp_str)

        new_transaction = MutualFundTransaction(
            fund_name=fund_name,
            transaction_type=transaction_type,
            amount=amount,
            units=units,
            nav=nav,
            timestamp=timestamp
        )
        db_session.add(new_transaction)
        db_session.commit()
        return redirect(url_for('show_transactions')) # Redirect to transactions page
    except Exception as e:
        db_session.rollback()
        return f"Error adding transaction: {e}", 500

@app.route('/edit_transaction/<int:transaction_id>', methods=['GET', 'POST'])
def edit_transaction(transaction_id):
    transaction = MutualFundTransaction.query.get(transaction_id)
    if request.method == 'POST':
        try:
            transaction.fund_name = request.form['fund_name']
            transaction.transaction_type = request.form['transaction_type']
            transaction.amount = float(request.form['amount'])
            transaction.units = float(request.form['units'])
            transaction.nav = float(request.form['nav'])
            timestamp_str = request.form['timestamp']
            transaction.timestamp = datetime.datetime.fromisoformat(timestamp_str)

            db_session.commit()
            return redirect(url_for('show_transactions')) # Redirect to transactions page
        except Exception as e:
            db_session.rollback()
            return f"Error updating transaction: {e}", 500
    return render_template('update_database.html', transaction=transaction)

@app.route('/new_transaction', methods=['GET', 'POST'])
def new_transaction():
    if request.method == 'POST':
        try:
            new_transaction = MutualFundTransaction(
                fund_name=request.form['fund_name'],
                transaction_type=request.form['transaction_type'],
                amount=float(request.form['amount']),
                units=float(request.form['units']),
                nav=float(request.form['nav']),
                timestamp=datetime.datetime.fromisoformat(request.form['timestamp'])
            )
            db_session.add(new_transaction)
            db_session.commit()
            return redirect(url_for('show_transactions')) # Redirect to transactions page
        except Exception as e:
            db_session.rollback()
            return f"Error adding transaction: {e}", 500
    return render_template('create_transaction.html')

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    transaction = MutualFundTransaction.query.get(transaction_id)
    if transaction:
        try:
            db_session.delete(transaction)
            db_session.commit()
            return redirect(url_for('show_transactions')) # Redirect to transactions page
        except Exception as e:
            db_session.rollback()
            return f"Error deleting transaction: {e}", 500
    return "Transaction not found", 404


if __name__ == '__main__':
    # Create the upload folder if it doesn't exist
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    with app.app_context():
        init_db() # Initialize the database within the app context

    app.run(debug=True)
