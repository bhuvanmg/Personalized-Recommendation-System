from flask import Flask, request, render_template, redirect, url_for, session, jsonify
from pymongo import MongoClient
import pymongo
from langchain_community.llms import LlamaCpp
from langchain.prompts import PromptTemplate
import requests

app = Flask(__name__)

app.secret_key = "your_secret_key"  #secret key for session management

uri = "mongodb+srv://bhuvan26602:2OVn18e8LkBa96mD@questt-db.ehngu3b.mongodb.net/?retryWrites=true&w=majority&appName=questt-db"
mongo_client = MongoClient(uri)
db = mongo_client["recommendation-system"]
users_collection = db["users"]

hf_token = "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"
embedding_url = "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2"

### USING OPENAI API. 

# os.environ['OPENAI_API_KEY'] = '----'
# chat_model = OpenAI(model="gpt-3.5-turbo-0125", temperature=0)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST', 'GET'])
def chat():
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']
        user = users_collection.find_one({'user_id': user_id, 'password': password})
        if user:
            session['user_id'] = user_id  # Save user_id in session
            return redirect(url_for('chat'))
        else:
            return "Invalid credentials"
    else:
        return render_template('chat.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']
        existing_user = users_collection.find_one({'user_id': user_id})
        if existing_user:
            return "User already exists"
        else:
            new_user = {'user_id': user_id, 'password': password, 'books': {'likes': [], 'dislikes': []},
                        'movies': {'likes': [], 'dislikes': []}, 'songs': {'likes': [], 'dislikes': []}}
            users_collection.insert_one(new_user)
            session['user_id'] = user_id
            return redirect(url_for('chat'))
    else:
        return render_template('register.html')

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' in session: 
        user_id = session['user_id']
        if 'user_id' in session:
            user = users_collection.find_one({'user_id': user_id})
            if user:
                return render_template('profile.html', user=user)
            else:
                return "User not found"
    else:
        return redirect(url_for('index'))

@app.route('/process_message', methods=['POST'])
def process_message():
    message = request.form['message']
    user_id = session.get('user_id')

    if(message=="match"):
        similar_users = matchuser(session['user_id'])
        return jsonify({'response': similar_users})
    elif message.lower() == "preferences":
        session['next_preference'] = 'category'
        return {'response': "Please specify your preference category: movies, songs, or books?"}
    elif session.get('next_preference') == 'category':
        if message.lower() in ['movies', 'songs', 'books']:
            session['preference_category'] = message.lower()
            session['next_preference'] = 'likes_dislikes'
            return {'response': f"Please specify if you like or dislike {message.lower()} (likes/dislikes):"}
        else:
            return {'response': "Invalid category. Please specify movies, songs, or books."}
    elif session.get('next_preference') == 'likes_dislikes':
        if message.lower() in ['likes', 'dislikes']:
            session['preference_likes_dislikes'] = message.lower()
            session['next_preference'] = 'names'
            return {'response': f"Please enter the names of your {message.lower()} for {session['preference_category']} (separated by commas):"}
        else:
            return {'response': "Invalid input. Please specify 'likes' or 'dislikes'."}
    elif session.get('next_preference') == 'names':
        names = [name.strip() for name in message.split(',') if name.strip()]
        if names:
            user_id = session['user_id']
            category = session['preference_category']
            likes_dislikes = session['preference_likes_dislikes']
            update_preferences(user_id, category, likes_dislikes, names)
            session.pop('next_preference', None)
            session.pop('preference_category', None)
            session.pop('preference_likes_dislikes', None)
            return {'response': "Your preferences have been updated!"}  
        else:
            return {'response': "Please enter at least one name."}
     
    llm = LlamaCpp(
    model_path="models/llama-2-7b-chat.Q4_0.gguf",
    n_gpu_layers=40,
    n_batch=512,  
    verbose=False,  )

    liked_movies, disliked_movies = fetch_user_preferences(user_id, 'movies')
    liked_songs, disliked_songs = fetch_user_preferences(user_id, 'songs')
    liked_books, disliked_books = fetch_user_preferences(user_id, 'books')

    likes = ", ".join(liked_movies + liked_songs + liked_books)
    dislikes = ", ".join(disliked_movies + disliked_songs + disliked_books)

    template = """
    [INST] <<SYS>>
    You are acting as a recommendation sytem.
    User preferences are as follows:
    The user likes the following movies,songs and books: {likes}
    and the user dislikes the following movies,songs and books: {dislikes}.
    Using this information, recommend the user movies or songs or books as specified in the query. output only the names of the recommendations seperated with commas. The recommendations should not include the ones mentioned in the users preferences.<</SYS>>
    
    {question}[/INST]
    """
    
    prompt = PromptTemplate(template=template, input_variables=["likes","dislikes","question"])

    llm_chain = prompt | llm
    question  = message

    recommendations = llm_chain.invoke({"likes": likes, "dislikes": dislikes, "question": question})

    return {'response': recommendations}
    
def update_preferences(user_id, category, status, names):

    if status.lower() == 'likes':
        update_operation = {'$addToSet': {category + '.likes': {'$each': names}}}
    elif status.lower() == 'dislikes':
        update_operation = {'$addToSet': {category + '.dislikes': {'$each': names}}}
    else:
        return  

    result = users_collection.update_one({'user_id': user_id}, update_operation)
    if result.modified_count == 0:
        users_collection.insert_one({'user_id': user_id, category: {status: names}})

    preferences = user_preferences(user_id)
    preferences_embedding = generate_embedding(preferences)

    users_collection.update_one({'user_id': user_id}, {'$set': {'preferences_embedding': preferences_embedding}})
    

def fetch_user_preferences(user_id, category):
    # Fetch user's liked and disliked categories from MongoDB
    user = users_collection.find_one({'user_id': user_id})
    if user:
        liked = user.get(category, {}).get('likes', [])
        disliked = user.get(category, {}).get('dislikes', [])
        return liked, disliked
    else:
        return [], []

def generate_embedding(text: str) -> list[float]:

  response = requests.post(
    embedding_url,
    headers={"Authorization": f"Bearer {hf_token}"},
    json={"inputs": text})

  if response.status_code != 200:
    raise ValueError(f"Request failed with status code {response.status_code}: {response.text}")

  return response.json()

def user_preferences(user_id):
    user = users_collection.find_one({'user_id': user_id})
    liked_movies, disliked_movies = fetch_user_preferences(user_id, 'movies')
    liked_songs, disliked_songs = fetch_user_preferences(user_id, 'songs')
    liked_books, disliked_books = fetch_user_preferences(user_id, 'books')

    preferences = liked_movies + liked_songs + liked_books + disliked_movies + disliked_songs + disliked_books
    preferences = ' '.join(preferences)
    return preferences
    
def matchuser(user_id):
    user = users_collection.find_one({'user_id': user_id})
    if user:
        liked_movies, disliked_movies = fetch_user_preferences(user_id, 'movies')
        liked_songs, disliked_songs = fetch_user_preferences(user_id, 'songs')
        liked_books, disliked_books = fetch_user_preferences(user_id, 'books')

        preferences = liked_movies + liked_songs + liked_books + disliked_movies + disliked_songs + disliked_books
        preferences = ' '.join(preferences)
        if not preferences:
            return []

        results = users_collection.aggregate([
        {"$vectorSearch": {
            "queryVector": generate_embedding(preferences),
            "path": "preferences_embedding",
            "numCandidates": 100,
            "limit": 4,
            "index": "matchusers",
            }}
        ])
        similar_users = [result['user_id'] for result in results]
        similar_users.remove(user_id)
        return similar_users

if __name__ == '__main__':
    app.run(debug=True)
