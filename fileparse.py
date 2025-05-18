import pandas as pd
import tabula
from models import AccountBalance, Fund, MutualFundTransaction
import datetime
from fuzzywuzzy import process
import json
import requests
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy import create_engine

# Assuming DATABASE_URI and engine/db_session setup might be needed here if not passed
# or if these functions are called independently. For now, keeping minimal imports.

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


def process_excel_data(db_session, mutual_funds_filepath, account_balances_filepath, commit_changes=True):
    """
    Processes mutual fund and account balance files.
    If commit_changes is False, changes are not committed to the database.
    Returns a dict with keys:
        'last_mutual_fund_transactions': list of last few MutualFundTransaction entries,
        'new_mutual_fund_transactions': list of new MutualFundTransaction entries to be added,
        'last_account_balances': list of last few AccountBalance entries,
        'new_account_balances': list of new AccountBalance entries to be added,
        'error': error message if any,
        'success': boolean indicating success
    """
    result = {
        'last_mutual_fund_transactions': [],
        'new_mutual_fund_transactions': [],
        'last_account_balances': [],
        'new_account_balances': [],
        'error': None,
        'success': False
    }
    try:
        new_mutual_fund_transactions = []
        new_account_balances = []

        if mutual_funds_filepath != '':
            try:
                # Read Mutual Fund Transactions
                mutual_funds_xls = pd.ExcelFile(mutual_funds_filepath, engine='openpyxl')
                mutual_funds_df = mutual_funds_xls.parse('SWASTIK_9469790', skiprows=3)  # Read from the specified sheet name and skip header rows
                mutual_funds_df['Trade Date'] = pd.to_datetime(mutual_funds_df['Trade Date'])
                fund_code_mapping = load_fund_codes()
                unique_fund_names = mutual_funds_df['Investment name'].unique()

                for fund_name in unique_fund_names:
                    fund_code = fund_code_mapping.get(fund_name.lower())  # Get fund code from mapping (case-insensitive)

                    if not fund_code:
                        # If direct match not found, try fuzzy matching
                        best_match, score = process.extractOne(fund_name.lower(), fund_code_mapping.keys())
                        if score > 80:  # Use a threshold, e.g., 80
                            fund_code = fund_code_mapping.get(best_match)
                            print(f"Fuzzy matched '{fund_name}' to '{best_match}' with score {score}. Using code {fund_code}")
                        else:
                            print(f"Fund code not found for '{fund_name}' and no good fuzzy match found (best match: '{best_match}', score: {score})")

                    # Add or update the Fund table
                    fund_entry = db_session.query(Fund).filter_by(fund_name=fund_name).first()
                    if fund_entry:
                        if fund_code and not fund_entry.fund_code:  # Update fund_code if it was missing
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

                if commit_changes:
                    db_session.commit()  # Commit fund updates

                # Get latest transaction date from database
                latest_transaction = db_session.query(MutualFundTransaction).order_by(MutualFundTransaction.timestamp.desc()).first()
                latest_date = latest_transaction.timestamp if latest_transaction else None

                # Filter transactions to only those after latest_date
                if latest_date:
                    filtered_mutual_funds_df = mutual_funds_df[mutual_funds_df['Trade Date'] > latest_date]
                else:
                    filtered_mutual_funds_df = mutual_funds_df

                # Prepare Mutual Fund Transactions
                for index, row in filtered_mutual_funds_df.iterrows():
                    fund_name = row['Investment name']

                    transaction_type = None
                    amount = 0.0
                    units = 0.0
                    nav = 0.0  # NAV is missing, setting to 0 for now

                    if row.get('Buy units', 0) > 0:
                        transaction_type = 'Buy'
                        units = row['Buy units']
                        amount = row.get('Cash inflow', 0)
                    elif row.get('Sell units', 0) > 0:
                        transaction_type = 'Sell'
                        units = row['Sell units']
                        amount = row.get('Cash outflow', 0)
                    elif row.get('Dividend reinvested units', 0) > 0:
                        transaction_type = 'Buy'  # Reinvestment is a form of buying units
                        units = row['Dividend reinvested units']
                        amount = row.get('Dividend Amount', 0)

                    calculated_nav = 0.0
                    if units != 0:
                        # Use absolute value of amount for NAV calculation for sell transactions
                        nav_amount = abs(amount) if transaction_type == 'Sell' else amount
                        calculated_nav = nav_amount / units

                    if transaction_type:  # Only process if a transaction type is determined
                        transaction_entry = MutualFundTransaction(
                            fund_name=fund_name,
                            transaction_type=transaction_type,
                            amount=amount,
                            units=units,
                            nav=calculated_nav,  # Use calculated NAV
                            timestamp=row['Trade Date'])
                        new_mutual_fund_transactions.append(transaction_entry)
                        if commit_changes:
                            db_session.add(transaction_entry)

                # Get last few mutual fund transactions for display
                last_mutual_fund_transactions = db_session.query(MutualFundTransaction).order_by(MutualFundTransaction.timestamp.desc()).limit(10).all()
                result['last_mutual_fund_transactions'] = last_mutual_fund_transactions
                result['new_mutual_fund_transactions'] = new_mutual_fund_transactions

            except Exception as e:
                result['error'] = f"Error reading Mutual Funds file: {e}"
                return result

        # Account Balances processing
        if account_balances_filepath != '' and account_balances_filepath.split('.')[1].lower() == 'xlsx':
            try:
                account_balances_df = pd.read_excel(account_balances_filepath, engine='openpyxl')
                account_balances_df['Date'] = pd.to_datetime(account_balances_df['Date'])
                # Get latest date from database for account balances
                latest_balance_entry = db_session.query(AccountBalance).order_by(AccountBalance.date.desc()).first()
                latest_date = latest_balance_entry.date if latest_balance_entry else None
                # Filter to only new entries after latest_date
                if latest_date:
                    filtered_account_balances_df = account_balances_df[account_balances_df['Date'] > latest_date]
                else:
                    filtered_account_balances_df = account_balances_df
                new_account_balances = []
                for index, row in filtered_account_balances_df.iterrows():
                    balance_entry = AccountBalance(
                        bank=row['Bank'],
                        closing_balance=row['Closing Balance'],
                        date=row['Date'],
                        narration=row['Narration'],
                        chq_ref_no=row['Chq./Ref.No.'],
                        withdrawal_amt=row['Withdrawal Amt.'],
                        deposit_amt=row['Deposit Amt.'],
                    )
                    new_account_balances.append(balance_entry)
                    if commit_changes:
                        db_session.add(balance_entry)

                # Get last few account balances for display
                last_account_balances = db_session.query(AccountBalance).order_by(AccountBalance.date.desc()).limit(10).all()
                result['last_account_balances'] = last_account_balances
                result['new_account_balances'] = new_account_balances

                if commit_changes:
                    db_session.commit()
                result['success'] = True
                return result
            except Exception as e:
                result['error'] = f"Error processing Account Balances file: {e}"
                return result
        elif account_balances_filepath != '' and account_balances_filepath.split('.')[1].lower() == 'pdf':
            try:
                df = tabula.read_pdf(account_balances_filepath, pages=1, pandas_options={'header': 0})
                df2 = tabula.read_pdf(account_balances_filepath, pages='all', pandas_options={'header': None})
                df1 = pd.DataFrame()
                for sdf in df2:
                    sdf.columns = df[0].columns
                    df1 = pd.concat([df1, sdf], ignore_index=True)
                df1 = df1[1:]
                df1['Date'] = pd.to_datetime(df1['Date'], format='%d-%m-%Y')
                df1['Amount'] = df1['Amount'].astype('float')
                df1 = df1.sort_values('Date', ascending=True)
                latest_balance_entry = db_session.query(AccountBalance).filter(AccountBalance.bank == 'ICICI').order_by(AccountBalance.date.desc()).first()
                latest_date = latest_balance_entry.date if latest_balance_entry else None
                latest_balance = latest_balance_entry.closing_balance if latest_balance_entry else 0
                # Filter to only new entries after latest_date
                if latest_date:
                    df1 = df1[df1['Date'] > latest_date]
                df1['net'] = df1.apply(lambda row: row['Amount'] * -1 if row['Type'] == 'DR' else row['Amount'], axis=1)
                print(latest_balance, df1)
                df1['Balance'] = latest_balance + df1['net'].cumsum()
                new_account_balances = []
                df2 = df1[df1['Type'] == 'CR']
                for index, row in df2.iterrows():
                    balance_entry = AccountBalance(
                        bank='ICICI',
                        date=row['Date'],
                        narration=row['Description'],
                        withdrawal_amt=0,
                        deposit_amt=row['Amount'],
                        closing_balance=row['Balance'],
                    )
                    new_account_balances.append(balance_entry)
                    if commit_changes:
                        db_session.add(balance_entry)
                df2 = df1[df1['Type'] == 'DR']
                for index, row in df2.iterrows():
                    balance_entry = AccountBalance(
                        bank='ICICI',
                        date=row['Date'],
                        narration=row['Description'],
                        withdrawal_amt=row['Amount'],
                        deposit_amt=0,
                        closing_balance=row['Balance']
                    )
                    new_account_balances.append(balance_entry)
                    if commit_changes:
                        db_session.add(balance_entry)
                print(df2)
                # Get last few account balances for display
                last_account_balances = db_session.query(AccountBalance).order_by(AccountBalance.date.desc()).limit(10).all()
                result['last_account_balances'] = last_account_balances
                result['new_account_balances'] = new_account_balances

                if commit_changes:
                    db_session.commit()
                result['success'] = True
                return result
            except Exception as e:
                result['error'] = f"Error processing Account Balances file: {e}"
                return result

        result['success'] = True
        return result

    except Exception as e:
        db_session.rollback()
        result['error'] = f"Error processing file: {e}"
        return result

def commit_processed_data(db_session, mutual_fund_transactions, account_balances):
    """Commits a list of processed transactions and balances to the database."""
    try:
        for transaction in mutual_fund_transactions:
            db_session.add(transaction)
        for balance in account_balances:
            db_session.add(balance)
        db_session.commit()
        return {'success': True, 'error': None}
    except Exception as e:
        db_session.rollback()
        return {'success': False, 'error': f"Error committing data: {e}"}
