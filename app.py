import os
from flask import Flask, request, redirect, url_for, render_template, flash, get_flashed_messages
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, inspect
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from models import Base, AccountBalance, Fund, MutualFundTransaction, FixedDeposit
import pandas as pd
import sys
import json
import requests
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from pyxirr import xirr
import datetime
from sqlalchemy import text,func
import locale
from fileparse import *

locale.setlocale(locale.LC_ALL, '')
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xlsx','pdf'}
DATABASE_URI = 'sqlite:///finances.db'

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'super secret key' # Replace with a real secret key

engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))
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

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'mutual_funds_file' not in request.files or 'account_balances_file' not in request.files:
            return "Missing one or both files", 400

        mutual_funds_file = request.files['mutual_funds_file']
        account_balances_file = request.files['account_balances_file']

        if mutual_funds_file.filename == '' and account_balances_file.filename == '':
            return "One or both files have no selected file", 400

        mutual_funds_filepath = ''
        account_balances_filepath = ''
        mutual_funds_filename = ''
        account_balances_filename = ''

        if mutual_funds_file and allowed_file(mutual_funds_file.filename):
            mutual_funds_filename = secure_filename(mutual_funds_file.filename)
            mutual_funds_filepath = os.path.join(app.config['UPLOAD_FOLDER'], mutual_funds_filename)
            mutual_funds_file.save(mutual_funds_filepath)

        if account_balances_file and allowed_file(account_balances_file.filename):
            account_balances_filename = secure_filename(account_balances_file.filename)
            account_balances_filepath = os.path.join(app.config['UPLOAD_FOLDER'], account_balances_filename)
            account_balances_file.save(account_balances_filepath)

        if not mutual_funds_filepath and not account_balances_filepath:
            flash('Invalid file type for one or both files', 'danger')
            return redirect(url_for('upload_file')) # Redirect back to the upload page

        pdf_password = request.form.get('pdf_password')

        # Process files without committing to get new and last entries
        result = process_excel_data(db_session, mutual_funds_filepath, account_balances_filepath, password=pdf_password, commit_changes=False)

        if result.get('error'):
            flash(f"Error processing files: {result['error']}", 'danger')
            return redirect(url_for('upload_file'))

        # Render confirmation page with last and new entries
        return render_template('confirm_upload.html',
                               last_mutual_fund_transactions=result.get('last_mutual_fund_transactions', []),
                               new_mutual_fund_transactions=result.get('new_mutual_fund_transactions', []),
                               last_account_balances=result.get('last_account_balances', []),
                               new_account_balances=result.get('new_account_balances', []),
                               mutual_funds_file=mutual_funds_filename,
                               account_balances_file=account_balances_filename)

    # Existing GET logic unchanged
    # Fetch latest account balance for each bank and sum their closing balances
    from sqlalchemy import func, and_, select
    # Subquery to get max date per bank
    max_date_subq = db_session.query(
        AccountBalance.bank,
        func.max(AccountBalance.date).label('max_date')
    ).group_by(AccountBalance.bank).subquery()

    # Subquery to get max id per bank and max date (to uniquely identify latest entry)
    max_id_subq = db_session.query(
        AccountBalance.bank,
        AccountBalance.date,
        func.max(AccountBalance.id).label('max_id')
    ).join(
        max_date_subq,
        and_(
            AccountBalance.bank == max_date_subq.c.bank,
            AccountBalance.date == max_date_subq.c.max_date
        )
    ).group_by(AccountBalance.bank, AccountBalance.date).subquery()

    # Query to get latest unique entries per bank by joining on bank, date, and id
    latest_balances = db_session.query(AccountBalance).join(
        max_id_subq,
        and_(
            AccountBalance.bank == max_id_subq.c.bank,
            AccountBalance.date == max_id_subq.c.date,
            AccountBalance.id == max_id_subq.c.max_id
        )
    ).all()
    latest_balance = sum(balance.closing_balance for balance in latest_balances) if latest_balances else 0
    
    # Calculate total current value of mutual funds
    fund_performance = {}
    transactions = MutualFundTransaction.query.order_by(MutualFundTransaction.timestamp).all()
    funds = db_session.query(Fund).all()
    fund_info = {fund.fund_name: {'fund_code': fund.fund_code, 'current_nav': fund.current_nav} for fund in funds}

    for transaction in transactions:
        fund_name = transaction.fund_name
        if fund_name not in fund_performance:
            fund_performance[fund_name] = {
                'total_units': 0,
                'current_nav': fund_info.get(fund_name, {}).get('current_nav', 0.0)
            }
        if transaction.transaction_type.lower() == 'buy':
            fund_performance[fund_name]['total_units'] += transaction.units
        elif transaction.transaction_type.lower() == 'sell':
            fund_performance[fund_name]['total_units'] -= transaction.units

    total_mutual_fund_value = sum(fund['total_units'] * fund['current_nav'] for fund in fund_performance.values() if fund['current_nav'] is not None)

    # Calculate total fixed deposit amount
    total_fixed_deposit_amount = db_session.query(func.sum(FixedDeposit.amount)).scalar() or 0

    # Calculate total portfolio net worth
    total_net_worth = locale.currency(int(latest_balance + total_mutual_fund_value + total_fixed_deposit_amount),grouping=True).split('.')[0]

    return render_template('index.html', latest_balance=locale.currency(latest_balance,grouping=True).split('.')[0], 
                        total_mutual_fund_value=locale.currency((total_mutual_fund_value), grouping=True).split('.')[0],
                        total_fixed_deposit_amount=locale.currency((total_fixed_deposit_amount), grouping=True).split('.')[0],
                        total_net_worth=total_net_worth)

@app.route('/confirm_upload', methods=['POST'])
def confirm_upload():
    confirm = request.form.get('confirm')
    mutual_funds_file = request.form.get('mutual_funds_file', '')
    account_balances_file = request.form.get('account_balances_file', '')

    if confirm == 'yes':
        mutual_funds_filepath = os.path.join(app.config['UPLOAD_FOLDER'], mutual_funds_file) if mutual_funds_file else ''
        account_balances_filepath = os.path.join(app.config['UPLOAD_FOLDER'], account_balances_file) if account_balances_file else ''

        # Process files with commit_changes=True to commit to database
        result = process_excel_data(db_session, mutual_funds_filepath, account_balances_filepath, commit_changes=True)

        if result.get('error'):
            flash(f"Error committing data: {result['error']}", 'danger')
        else:
            flash('Data successfully committed to the database.', 'success')
    else:
        flash('Upload cancelled. No changes were made.', 'info')

    return redirect(url_for('upload_file'))

@app.route('/balances')
def show_balances():
    account_balances = AccountBalance.query.order_by(AccountBalance.date.asc()) # Order by date ascending for chart
    acdf= pd.read_sql(account_balances.statement, account_balances.session.bind)
    acdf = acdf.sort_values('date')
    acdf['month'] = acdf['date'].dt.to_period('M')
    acdf=acdf.groupby(['bank','month']).last().reset_index()[['bank','month','closing_balance']]
    account_balances_data = []
    for i,balance in acdf.iterrows():
        account_balances_data.append({
            'date': balance.month.strftime('%Y-%m-%d'), # Format date as string
            'closing_balance': balance.closing_balance,
            'bank': balance.bank
        })
    return render_template('balances.html', account_balances=db_session.execute(text('SELECT bank, MAX(date) as date, closing_balance FROM account_balances GROUP BY bank')), account_balances_data=account_balances_data)

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

@app.route('/fixed_deposits', methods=['GET'])
def show_fixed_deposits():
    # Retrieve all fixed deposits, regardless of status
    fixed_deposits = FixedDeposit.query.order_by(FixedDeposit.maturity_date.asc()).all()
    return render_template('fixed_deposits.html', fixed_deposits=fixed_deposits)

@app.route('/new_fixed_deposit', methods=['GET', 'POST'])
def new_fixed_deposit():
    if request.method == 'POST':
        try:
            new_fd = FixedDeposit(
                bank=request.form['bank'],
                amount=float(request.form['amount']),
                interest_rate=float(request.form['interest_rate']),
                start_date=datetime.datetime.fromisoformat(request.form['start_date'])
            )

            duration = int(request.form['duration'])
            duration_unit = request.form['duration_unit']
            start_date = new_fd.start_date

            # Calculate maturity date based on duration unit
            if duration_unit == 'years':
                maturity_date = start_date + datetime.timedelta(days=duration * 365.25) # Approximate years
            elif duration_unit == 'months':
                # This is a simplified approach for months, a more robust solution might be needed
                maturity_date = start_date + datetime.timedelta(days=duration * 30.44) # Approximate months
            elif duration_unit == 'days':
                maturity_date = start_date + datetime.timedelta(days=duration)
            else:
                return "Invalid duration unit", 400 # Should not happen with dropdown

            new_fd.maturity_date = maturity_date

            db_session.add(new_fd)
            db_session.commit()
            flash('Fixed Deposit added successfully!', 'success')
            return redirect(url_for('show_fixed_deposits'))
        except Exception as e:
            db_session.rollback()
            flash(f"Error adding fixed deposit: {e}", 'danger')
            return f"Error adding fixed deposit: {e}", 500
    return render_template('create_fixed_deposit.html')

@app.route('/edit_fixed_deposit/<int:fd_id>', methods=['GET', 'POST'])
def edit_fixed_deposit(fd_id):
    fd = FixedDeposit.query.get(fd_id)
    if request.method == 'POST':
        try:
            fd.bank = request.form['bank']
            fd.amount = float(request.form['amount'])
            fd.interest_rate = float(request.form['interest_rate'])
            fd.start_date = datetime.datetime.fromisoformat(request.form['start_date'])
            fd.maturity_date = datetime.datetime.fromisoformat(request.form['maturity_date'])

            db_session.commit()
            return redirect(url_for('show_fixed_deposits'))
        except Exception as e:
            db_session.rollback()
            return f"Error updating fixed deposit: {e}", 500
    return render_template('edit_fixed_deposit.html', fd=fd)

@app.route('/delete_fixed_deposit/<int:fd_id>', methods=['POST'])
def delete_fixed_deposit(fd_id):
    fd = FixedDeposit.query.get(fd_id)
    if fd:
        try:
            db_session.delete(fd)
            db_session.commit()
            return redirect(url_for('show_fixed_deposits'))
        except Exception as e:
            db_session.rollback()
            return f"Error deleting fixed deposit: {e}", 500
    return "Fixed Deposit not found", 404

@app.route('/close_fixed_deposit/<int:fd_id>', methods=['POST'])
def close_fixed_deposit(fd_id):
    fd = FixedDeposit.query.get(fd_id)
    if fd:
        try:
            # Calculate interest earned
            # Assuming simple interest for the duration from start_date to closure_date
            # If compounding is needed, a more complex calculation is required.
            # For simplicity, using today's date as the closure date for calculation
            closure_date = datetime.datetime.now()
            duration_days = (closure_date - fd.start_date).days
            # Simple interest formula: Principal * Rate * Time / 100
            # Time in years = duration_days / 365.25 (to account for leap years)
            time_years = duration_days / 365.25
            total_interest = (fd.amount * fd.interest_rate * time_years) / 100

            fd.total_interest_earned = total_interest
            fd.status = 'closed' # Or 'matured' if closure_date >= maturity_date
            fd.closure_date = closure_date

            db_session.commit()
            flash(f'Fixed Deposit {fd_id} closed successfully. Interest earned: {total_interest:.2f}', 'success')
            return redirect(url_for('show_fixed_deposits'))
        except Exception as e:
            db_session.rollback()
            flash(f"Error closing fixed deposit {fd_id}: {e}", 'danger')
            return redirect(url_for('show_fixed_deposits'))
    flash(f"Fixed Deposit {fd_id} not found", 'danger')
    return "Fixed Deposit not found", 404


if __name__ == '__main__':
    # Create the upload folder if it doesn't exist
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    with app.app_context():
        init_db() # Initialize the database within the app context

app.run(debug=True)
