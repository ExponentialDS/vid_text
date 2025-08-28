## Deployment Note

⚠️ YouTube often blocks transcript requests from shared IP addresses (such as those used by Streamlit Cloud).  
As a result, this app may fail to fetch transcripts when deployed on Streamlit Cloud with an error like:

YouTube blocked the transcript request (rate limit, age/region lock).

### Workarounds
- Run the app **locally** with:
  ```bash
  streamlit run app.py
