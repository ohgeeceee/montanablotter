import imaplib
import email
import os
import config

# Deprecated helper: keep config-driven values so secrets are not stored in source.
EMAIL_USER = config.EMAIL_USER
EMAIL_PASS = config.EMAIL_PASSWORD
IMAP_SERVER = config.IMAP_SERVER
SAVE_DIR = config.UPLOAD_DIR

def fetch_attachments():
    try:
        # Connect to the server
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        # Search for UNSEEN (unread) emails
        status, messages = mail.search(None, 'UNSEEN')
        
        for num in messages[0].split():
            # Fetch the email body
            status, data = mail.fetch(num, '(RFC822)')
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Walk through the email parts
            for part in msg.walk():
                if part.get_content_maintype() == 'multipart':
                    continue
                if part.get('Content-Disposition') is None:
                    continue

                filename = part.get_filename()
                if filename and filename.lower().endswith('.pdf'):
                    filepath = os.path.join(SAVE_DIR, filename)
                    
                    # Save the PDF
                    with open(filepath, 'wb') as f:
                        f.write(part.get_payload(decode=True))
                    
                    print(f"Downloaded: {filename}")
                    
                    # Set permissions so the web portal can read it
                    os.chmod(filepath, 0o664)

        mail.close()
        mail.logout()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_attachments()
