import os
# os.environ["ANTHROPIC_API_KEY"] = "YOUR_API_KEY"
os.environ["PANDASAI_API_KEY"] = "$2a$10$FjU4oB5pNW2ycAgjeyM/b.3RlMyFgerehTKGrRzo3nS44hSAxewuK"

import pandas as pd
from typing import List, Dict, Any
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd
from anthropic import Anthropic
from os import getenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
import time
import random
from pandasai import Agent # This helps us chat with the dataframe


console = Console()
clean_line = lambda line: line.split("]")[-1].strip()

def prepare_str(conversations):
    """
    Prepare a formatted string of conversations for the user's query.

    Args:
        conversations (List[str]): A list of conversation strings.

    Returns:
        str: A formatted string containing numbered conversations.
    """
    formatted_conversations = [f"{i+1}: {conv}" for i, conv in enumerate(conversations)]
    return f"Here are the relevant conversations to the user's query:\n" + "\n".join(formatted_conversations)

def get_csv_and_excel_files(directory: str) -> List[str]:
    """
    Retrieve all CSV and Excel files from the specified directory.
    
    Args:
        directory (str): The path to the directory to search.
    
    Returns:
        List[str]: A list of file paths for CSV and Excel files.
    """
    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if filename.endswith(('.csv', '.xlsx', '.xls')):
                files.append(os.path.join(root, filename))
    return files

def read_file(file_path: str) -> pd.DataFrame:
    """
    Read a CSV or Excel file and return its contents as a pandas DataFrame.
    
    Args:
        file_path (str): The path to the file to read.
    
    Returns:
        pd.DataFrame: The contents of the file as a pandas DataFrame.
    """
    if file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
        df['text'] = df.apply(lambda x: clean_line(x['Line']), axis=1)
        return df
    elif file_path.endswith(('.xlsx', '.xls')):
        df = pd.read_excel(file_path)
        df['text'] = df.apply(lambda x: clean_line(x['Line']), axis=1)
        return df
    else:
        raise ValueError(f"Unsupported file format: {file_path}")

def retrieve_data(database_folder: str) -> Dict[str, Any]:
    """
    Retrieve all CSV and Excel files from the database folder and read their contents.
    
    Args:
        database_folder (str): The path to the database folder.
    
    Returns:
        Dict[str, Any]: A dictionary with file names as keys and their contents as values.
    """
    data = {}
    files = get_csv_and_excel_files(database_folder)
    
    for file_path in files:
        file_name = os.path.basename(file_path)
        try:
            data[file_name] = read_file(file_path)
            print(f"Successfully read {file_name}")
        except Exception as e:
            print(f"Error reading {file_name}: {str(e)}")
    
    return data


retrieve_tool = {
    "name": "retrieve",
    "description": "Retrieve relevant conversations from the database given a query",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's query to search for relevant conversations"
            },
            "top_k": {
                "type": "integer",
                "description": "Number of top relevant conversations to retrieve",
                "default": 5
            },
            "n_context": {
                "type": "integer",
                "description": "Number of context utterances to include",
                "default": 4
            }
        },
        "required": ["query"]
    }
}

data_analysis_tool = {
    "name": "data_analysis",
    "description": "Perform data analysis on the dataframe base on a query",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's query to perform data analysis"
            }
        }
    },
    "required": ["query"]
}

def get_claude_response(client, messages, tools, system_prompt):
    """ 
    Obtain response from Claude
    """
    if tools:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=tools
        )
    else:
        response = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1024,
            system=system_prompt,
            messages=messages
        )
    return response


class TinyChat:
    def __init__(self, database_folder):
        """ 
        TinyChat allows one to chat with local database interactively
        """
        self.database_folder = database_folder
        self.retrieved_data = self._get_data()
        self.combined_df = self._combine_data()
        self.pandas_agent = Agent(self.combined_df) # Pandas Agent AI
        self.model = self._setup_embedding_model()
        self.embeddings = self._create_embeddings()
        self.client = Anthropic(api_key=getenv("ANTHROPIC_API_KEY"))
        self.chat_prompt = "You are a FY, an AI that can help user understand the database better. You will discuss the retrieved conversation with user."
        self.rag_prompt = "You are a FY, an AI that can help user understand the database better. Make wise decision on whether to retrieve the database with queries."
        self.messages = []
        self.retrieved_conversations = ""

    def _get_data(self):
        retrieved_data = retrieve_data(self.database_folder)
        print(f"\nRetrieved {len(retrieved_data)} files:")
        for file_name, df in retrieved_data.items():
            print(f"{file_name}: {df.shape[0]} rows, {df.shape[1]} columns")
        return retrieved_data

    def _combine_data(self):
        return pd.concat(self.retrieved_data.values(), ignore_index=True)
    
    def _get_stat(self, query):
        """ 
        Chat with Pandas AI Agent for statistics & info in the dataframe
        """
        return self.pandas_agent.chat(query)

    def _setup_embedding_model(self):
        return SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

    def _create_embeddings(self):
        return self.model.encode(self.combined_df['text'].tolist(), show_progress_bar=True)

    def retrieve(self, query, top_k=5, n_context=4):
        """
        Retrieve the top-k relevant conversations given a query
        and include n_context number of utterances continuing from the retrieved query
        """
        query_embedding = self.model.encode([query])
        cosine_similarities = cosine_similarity(query_embedding, self.embeddings).flatten()
        top_indices = cosine_similarities.argsort()[-top_k:][::-1]

        top_conversations = []
        for idx in top_indices:
            start_idx = max(0, idx - n_context + 1)
            end_idx = min(len(self.combined_df), idx + 1)
            context = self.combined_df["text"].iloc[start_idx:end_idx].tolist()
            context = [line.replace("Recognized:", "Agent:").replace("Bot response:", "TrainingGuru:") for line in context]
            top_conversations.append("\n".join(context))

        return top_conversations
    
    def chat(self, query):
        """ 
        Chat with the database
        """
        self.messages.append({"role": "user", "content": query})
        response = get_claude_response(self.client, self.messages, tools=[retrieve_tool], system_prompt=self.rag_prompt)
        
        self.retrieved_conversations = ""
        
        for block in response.content:
            if block.type == "tool_use" and block.name=="retrieve":
                top_conversations = self.retrieve(**block.input)
                context = prepare_str(top_conversations)
                
                query = block.input['query']
                
                self.messages.append({
                    "role": "assistant",
                    "content": f"Performing retrieval with query {query} ...."
                })

                self.messages.append({
                    "role": "user",
                    "content": context,
                })
                
                self.retrieved_conversations = context

                response = get_claude_response(self.client, self.messages, tools=[], system_prompt=self.chat_prompt)

        
        assistant_response = response.content[0].text
        self.messages.append({"role": "assistant", "content": assistant_response})
        self.messages = self.messages[-8:] # Keep only last 8 rounds of conversation
        return assistant_response




def main():
    console.print(Panel("Hi! I'm FY, your personal RAG Agent here to help you chat with your database!", title="Welcome", style="bold green"))
    console.print("Type 'exit' to end the conversation.")

    # Initialize TinyChat with the database folder
    database_folder = "database"  # Replace with actual path
    tiny_chat = TinyChat(database_folder)

    while True:
        user_input = console.input("[bold cyan]You:[/bold cyan] ")

        if user_input.lower() == 'exit':
            console.print(Panel("Thank you for chatting. Goodbye!", title_align="left", title="Goodbye", style="bold green"))
            break
        
        # Use TinyChat to process the user's input and get a response
        response = tiny_chat.chat(user_input)
        
        # Display Retrieved Conversations
        if tiny_chat.retrieved_conversations:
            console.print(Panel("[bold green]Retrieved Conversations:[/bold green]", style="green"))
            conversations = tiny_chat.retrieved_conversations.split("\n")
            for i, conv in enumerate(conversations[1:], 1):  # Skip the first line as it's the header
                if conv.strip():  # Check if the line is not empty
                    color = f"color({i % 5 + 1})"  # Cycle through 5 different colors
                    console.print(Panel(Markdown(conv), border_style=color, expand=False))
                    time.sleep(random.uniform(0.1, 0.3))  # Random delay between 0.1 and 0.3 seconds

        # Display the assistant's response
        console.print(Panel.fit("[bold green]RAG-Agent:[/bold green]", style="green"))
        response_with_newlines = response.replace("\n", "  \n")  # Fix newlines for Markdown
        console.print(Panel(Markdown(response_with_newlines), style="green", expand=False))

if __name__ == "__main__":
    main()