import os
from flask import Flask, request, redirect, url_for, render_template
from werkzeug.utils import secure_filename
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import pandas as pd
import sys

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
        xls = pd.ExcelFile(filepath,engine='openpyxl')
        # Assuming sheets named 'Account Balances' and 'Mutual Funds'
        account_balances_df = xls.parse('Account Balances')
        mutual_funds_df = xls.parse('Mutual Funds')

        # Process Account Balances
        for index, row in account_balances_df.iterrows():
            balance_entry = AccountBalance(
                account_name=row['Account Name'],
                balance=row['Balance'],
                timestamp=row['Timestamp'] # Assuming timestamp is in a suitable format
            )
            db_session.add(balance_entry)

        # Process Mutual Fund Transactions
        for index, row in mutual_funds_df.iterrows():
            transaction_entry = MutualFundTransaction(
                fund_name=row['Fund Name'],
                transaction_type=row['Transaction Type'],
                amount=row['Amount'],
                units=row['Units'],
                nav=row['NAV'],
                timestamp=row['Timestamp'] # Assuming timestamp is in a suitable format
            )
            db_session.add(transaction_entry)

        db_session.commit()
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
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            if process_excel_data(filepath):
                return 'File successfully uploaded and data processed'
            else:
                return 'File uploaded but data processing failed'
        else:
            return 'Invalid file type'
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
    # This is a simplified calculation and needs more sophisticated logic
    # to handle different transaction types, splits, dividends, etc.
    fund_performance = {}
    transactions = MutualFundTransaction.query.order_by(MutualFundTransaction.timestamp).all()

    for transaction in transactions:
        if transaction.fund_name not in fund_performance:
            fund_performance[transaction.fund_name] = {
                'total_invested': 0,
                'total_units': 0,
                'transactions': []
            }

        fund_performance[transaction.fund_name]['transactions'].append(transaction)

        if transaction.transaction_type.lower() == 'buy':
            fund_performance[transaction.fund_name]['total_invested'] += transaction.amount
            fund_performance[transaction.fund_name]['total_units'] += transaction.units
        elif transaction.transaction_type.lower() == 'sell':
            fund_performance[transaction.fund_name]['total_invested'] -= transaction.amount # This is a simplification
            fund_performance[transaction.fund_name]['total_units'] -= transaction.units

    # For a real application, you would fetch the current NAV to calculate current value
    # and then calculate returns (absolute, CAGR).
    # For this example, we'll just pass the aggregated transaction data.

    return render_template('performance.html', fund_performance=fund_performance)

if __name__ == '__main__':
    # Create the upload folder if it doesn't exist
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    with app.app_context():
        init_db() # Initialize the database within the app context

    app.run(debug=True)
