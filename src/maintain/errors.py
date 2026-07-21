"""Typed failures with stable CLI exit codes."""


class MaintainError(RuntimeError):
    exit_code = 5


class ConfigurationError(MaintainError):
    exit_code = 2


class PolicyError(MaintainError):
    exit_code = 4


class ProviderError(MaintainError):
    exit_code = 3


class VerificationError(MaintainError):
    exit_code = 7


class RecoveryError(MaintainError):
    exit_code = 9


class ReviewError(MaintainError):
    exit_code = 6


class HumanActionRequired(MaintainError):
    exit_code = 8


class DeliveryError(MaintainError):
    exit_code = 10
