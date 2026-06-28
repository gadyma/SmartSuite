import os
import json
import boto3
from botocore.exceptions import ClientError

def get_aws_secret(secret_name, region_name="il-central-1"):
    """
    Fetches and parses a JSON secret from AWS Secrets Manager.
    Assumes the machine has the appropriate IAM role attached.
    """
    client = boto3.client("secretsmanager", region_name=region_name)
    
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        print(f"Error retrieving secret '{secret_name}': {e}")
        raise e

    # Parse and return the JSON string as a dictionary
    return json.loads(response["SecretString"])

# ==========================================
# USAGE IMPLEMENTATION
# ==========================================
if __name__ == "__main__":
    # 1. Define your specific target
    SECRET_ID = "secrets/prod/placeholder/config"
    REGION = "il-central-1"

    # 2. Fetch the secrets
    secrets = get_aws_secret(secret_name=SECRET_ID, region_name=REGION)

    # 3. Map your variables using .get() to avoid KeyErrors if a key is missing
    TOKEN = secrets.get("smartsuite_api_key")
    ACCOUNT_ID = secrets.get("smartsuite_workspace")

    # Google Drive Variables
    GOOGLE_SERVICE_ACCOUNT_JSON  = secrets.get("google_service_vaules_json")
    GOOGLE_SHARED_DRIVE_ID       = secrets.get("GOOGLE_SHARED_DRIVE_ID")
    GOOGLE_BACKUP_ROOT_FOLDER_ID = secrets.get("GOOGLE_BACKUP_ROOT_FOLDER_ID")
    GOOGLE_SHEET_PERMISSIONS_ID  = secrets.get("GOOGLE_SHEET_PERMISSIONS_ID")

