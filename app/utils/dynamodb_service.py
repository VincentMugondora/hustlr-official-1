import os
import boto3
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError
from datetime import datetime

class DynamoDBService:
    """Service for interacting with AWS DynamoDB tables"""
    
    def __init__(self):
        self.dynamodb = boto3.resource(
            'dynamodb',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        
        self.users_table = self.dynamodb.Table(os.getenv('AWS_DYNAMODB_USERS_TABLE', 'hustlr-users'))
        self.providers_table = self.dynamodb.Table(os.getenv('AWS_DYNAMODB_PROVIDERS_TABLE', 'hustlr-providers'))
        self.bookings_table = self.dynamodb.Table(os.getenv('AWS_DYNAMODB_BOOKINGS_TABLE', 'hustlr-bookings'))
    
    # User operations
    async def get_user(self, whatsapp_number: str) -> Optional[Dict]:
        """Get user by WhatsApp number"""
        try:
            response = self.users_table.get_item(Key={'whatsapp_number': whatsapp_number})
            return response.get('Item')
        except ClientError as e:
            print(f"Error getting user: {e}")
            return None
    
    async def create_user(self, user_data: Dict) -> bool:
        """Create new user"""
        try:
            user_data['created_at'] = datetime.utcnow().isoformat()
            self.users_table.put_item(Item=user_data)
            return True
        except ClientError as e:
            print(f"Error creating user: {e}")
            return False
    
    async def update_user(self, whatsapp_number: str, update_data: Dict) -> bool:
        """Update user information"""
        try:
            update_expression = "SET " + ", ".join([f"{k} = :{k}" for k in update_data.keys()])
            expression_values = {f":{k}": v for k, v in update_data.items()}
            
            self.users_table.update_item(
                Key={'whatsapp_number': whatsapp_number},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            return True
        except ClientError as e:
            print(f"Error updating user: {e}")
            return False
    
    # Provider operations
    async def get_providers_by_service(self, service_type: str, location: str = None) -> List[Dict]:
        """Get providers by service type and optionally location"""
        try:
            if location:
                # Scan for service type and location
                response = self.providers_table.scan(
                    FilterExpression="service_type = :service AND contains(location, :location)",
                    ExpressionAttributeValues={
                        ':service': service_type,
                        ':location': location
                    }
                )
            else:
                # Scan for service type only
                response = self.providers_table.scan(
                    FilterExpression="service_type = :service",
                    ExpressionAttributeValues={':service': service_type}
                )
            
            return response.get('Items', [])
        except ClientError as e:
            print(f"Error getting providers: {e}")
            return []
    
    async def create_provider(self, provider_data: Dict) -> bool:
        """Create new provider"""
        try:
            provider_data['created_at'] = datetime.utcnow().isoformat()
            provider_data['status'] = 'pending'  # Default status
            self.providers_table.put_item(Item=provider_data)
            return True
        except ClientError as e:
            print(f"Error creating provider: {e}")
            return False
    
    # Booking operations
    async def create_booking(self, booking_data: Dict) -> bool:
        """Create new booking"""
        try:
            booking_data['created_at'] = datetime.utcnow().isoformat()
            booking_data['status'] = 'pending'  # Default status
            self.bookings_table.put_item(Item=booking_data)
            return True
        except ClientError as e:
            print(f"Error creating booking: {e}")
            return False
    
    async def get_user_bookings(self, user_whatsapp_number: str) -> List[Dict]:
        """Get all bookings for a user"""
        try:
            response = self.bookings_table.scan(
                FilterExpression="user_whatsapp_number = :user_number",
                ExpressionAttributeValues={':user_number': user_whatsapp_number}
            )
            return response.get('Items', [])
        except ClientError as e:
            print(f"Error getting user bookings: {e}")
            return []
    
    async def update_booking_status(self, booking_id: str, status: str) -> bool:
        """Update booking status"""
        try:
            self.bookings_table.update_item(
                Key={'booking_id': booking_id},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': status}
            )
            return True
        except ClientError as e:
            print(f"Error updating booking status: {e}")
            return False
