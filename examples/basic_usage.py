#!/usr/bin/env python3
"""
Basic usage examples for SmartSuite tools.
"""

import sys
from pathlib import Path

# Add the parent directory to the path so we can import smartsuite
sys.path.insert(0, str(Path(__file__).parent.parent))

from smartsuite.backup import SmartSuiteBackup
from smartsuite.permissions import SmartSuitePermissionsAudit
from smartsuite.config import config


def example_backup():
    """Example of running a backup."""
    print("=== Running SmartSuite Backup ===")
    
    try:
        backup = SmartSuiteBackup()
        backup.run_full_backup()
        print("Backup completed successfully!")
        
    except Exception as e:
        print(f"Backup failed: {e}")


def example_permissions_audit():
    """Example of running a permissions audit."""
    print("=== Running SmartSuite Permissions Audit ===")
    
    try:
        audit = SmartSuitePermissionsAudit()
        audit.run_permissions_audit()
        print("Permissions audit completed successfully!")
        
    except Exception as e:
        print(f"Permissions audit failed: {e}")


def example_config_check():
    """Example of checking configuration."""
    print("=== Checking Configuration ===")
    
    if config.validate():
        print("Configuration is valid!")
        print(f"Account ID: {config.account_id}")
        print(f"Base URL: {config.base_url}")
        print(f"Default backup folder: {config.dest_folder}")
    else:
        print("Configuration is invalid!")
        print("Please check your API credentials.")


def example_custom_backup():
    """Example of running a backup to a custom location."""
    print("=== Running Custom Backup ===")
    
    try:
        # Create a custom backup instance
        backup = SmartSuiteBackup()
        
        # Override the backup path
        custom_path = Path.home() / "Desktop" / "custom_smartsuite_backup"
        backup.backup_path = custom_path
        backup.backup_path.mkdir(parents=True, exist_ok=True)
        
        print(f"Backing up to custom location: {custom_path}")
        backup.run_full_backup()
        print("Custom backup completed successfully!")
        
    except Exception as e:
        print(f"Custom backup failed: {e}")


def main():
    """Main function to run examples."""
    print("SmartSuite Tools - Basic Usage Examples")
    print("=" * 50)
    
    # Check configuration first
    example_config_check()
    print()
    
    if not config.validate():
        print("Skipping backup examples due to invalid configuration.")
        return
    
    # Run examples
    example_backup()
    print()
    
    example_permissions_audit()
    print()
    
    example_custom_backup()
    print()
    
    print("All examples completed!")


if __name__ == "__main__":
    main()

