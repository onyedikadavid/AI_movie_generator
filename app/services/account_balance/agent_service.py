"""Service for managing a simple account balance workflow."""

from __future__ import annotations


class AccountBalanceAgentService:
    """Simple balance manager used by an account-balance agent."""

    def __init__(self, initial_balance: float = 0.0):
        self.balance = float(initial_balance)

    def get_balance(self) -> float:
        """Return the current balance."""
        return self.balance

    def deposit(self, amount: float) -> float:
        """Add funds to the account and return the updated balance."""
        if amount < 0:
            raise ValueError("Deposit amount cannot be negative.")
        self.balance += float(amount)
        return self.balance

    def withdraw(self, amount: float) -> float:
        """Subtract funds from the account and return the updated balance."""
        if amount < 0:
            raise ValueError("Withdrawal amount cannot be negative.")
        if amount > self.balance:
            raise ValueError("Insufficient funds for this withdrawal.")
        self.balance -= float(amount)
        return self.balance

    def get_balance_message(self) -> str:
        """Return a simple readable balance summary."""
        return f"Your account balance is ${self.balance:,.2f}."
