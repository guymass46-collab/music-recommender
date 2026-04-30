# 🎵 Music Recommender

אפליקציית גילוי מוזיקה אישית מבוססת Last.fm.
מקבלת רשימת אמנים אהובים ומחזירה המלצות על אמנים דומים בינלאומיים.

---

## 🏗️ ארכיטקטורה

```
music-app/
├── api/
│   └── recommendations.py    # Serverless function (Python)
├── public/
│   └── index.html            # ממשק משתמש (סטטי)
├── .gitignore                # קבצים שלא עולים ל-Git
├── .env.example              # תבנית למשתני סביבה
├── requirements.txt          # תלויות Python (ריק - stdlib בלבד)
├── vercel.json               # הגדרות Vercel
└── README.md
```

**סודות**: ה-API key נטען ממשתני סביבה — לא קיים בקוד.
**אחסון**: רשימת האמנים נשמרת ב-localStorage של הדפדפן (כל משתמש מקבל את הרשימה האישית שלו).

---

## 🚀 התקנה ופיתוח מקומי

### 1. שכפול הפרויקט
```powershell
cd C:\Users\Guyma\Documents
# (או כל תיקייה אחרת)
```

### 2. יצירת קובץ `.env`
```powershell
copy .env.example .env
```
פתח את `.env` בעורך ושים את ה-API key האמיתי במקום `your_lastfm_api_key_here`.

### 3. הרצה מקומית עם Vercel CLI
```powershell
npm install -g vercel
vercel dev
```
האפליקציה תרוץ על `http://localhost:3000`.

---

## ☁️ העלאה ל-GitHub + Vercel

### שלב 1: יצירת ריפו ב-GitHub

1. כנס ל-https://github.com/new
2. שם: `music-recommender`
3. **בחר Private** (חשוב!)
4. **אל תוסיף** README/license/.gitignore (כבר יש לנו)
5. Create repository

### שלב 2: העלאה ראשונה (PowerShell)

```powershell
cd C:\Users\Guyma\Documents\music-app

# אתחל ריפו
git init
git branch -M main

# בדוק מה עולה - חובה!
git add .
git status
```

**🚨 בדיקת אבטחה לפני commit:**
```powershell
# שורה זו חייבת להחזיר ריק:
git status | Select-String "\.env$"

# שורה זו חייבת להחזיר ריק:
git diff --cached | Select-String "365be972"
```

אם אחת מהבדיקות מחזירה תוצאה — **עצור! המפתח חשוף**. אל תמשיך.

```powershell
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/music-recommender.git
git push -u origin main
```

### שלב 3: חיבור ל-Vercel

1. כנס ל-https://vercel.com/signup והירשם עם חשבון GitHub
2. **Add New → Project**
3. בחר את `music-recommender`
4. **לפני שלוחצים Deploy** — הוסף משתני סביבה:
   - **Settings → Environment Variables**
   - הוסף: `LASTFM_API_KEY` = ה-key שלך
   - הוסף: `ALLOWED_ORIGIN` = `*` (זמני, נשנה אחרי הדיפלוי)
5. לחץ **Deploy**
6. אחרי 1-2 דקות תקבל URL כמו `https://music-recommender-abc123.vercel.app`

### שלב 4: סגירת CORS

אחרי שיש URL פרודקשן:
1. חזור ל-Vercel → Settings → Environment Variables
2. עדכן `ALLOWED_ORIGIN` ל-URL האמיתי שלך
3. Deployments → ... → Redeploy

---

## 🛡️ אבטחה

### ✅ מה הקוד עושה
- API key רק במשתני סביבה (לא בקוד)
- מגבלת 30 אמנים מקסימום בבקשה
- מגבלת 100 תווים על שם אמן
- מגבלת 10KB על body של בקשה
- HTML escape על קלט משתמש (מונע XSS)
- CORS מוגבל לדומיין שלך (אחרי שלב 4)

### ⚠️ מה עוד מומלץ להוסיף
- **Rate limiting** דרך [Upstash](https://upstash.com) (חינם) - מונע ניצול לרעה
- **Vercel Web Analytics** - לראות שימוש חריג
- **GitHub Secret Scanning** - מופעל אוטומטית בריפוז Public, מומלץ גם ב-Private

### 💸 סיכון חיוב
- **Last.fm**: API חינמי לחלוטין, ללא חיוב.
- **Vercel**: Hobby tier מספיק — 100GB bandwidth ו-100K function invocations בחודש בחינם.
- אם בעתיד תוסיף Spotify/OpenAI/AWS — אלה בתשלום וחשוב להגדיר billing alerts.

---

## 🎨 שימוש

1. פתח את האתר בדפדפן
2. הוסף/הסר אמנים (הרשימה נשמרת אוטומטית)
3. לחץ "✨ גלה לי מוזיקה חדשה"
4. אחרי 15-30 שניות תקבל עד 25 המלצות עם:
   - תמונת אמן + ציון התאמה
   - תגיות ז'אנר
   - 3 שירים מובילים
   - לינקים ישירים ל-Spotify ו-SoundCloud

---

## 🐛 בעיות נפוצות

**"Server misconfiguration: API key missing"**
→ לא הגדרת `LASTFM_API_KEY` ב-Vercel. לך ל-Settings → Environment Variables.

**"לא נמצאו המלצות"**
→ נסה אמנים מוכרים יותר. Last.fm לא מכיר את כל האמנים הישראליים הקטנים.

**טעינה ארוכה מ-30 שניות**
→ Vercel Hobby tier מגביל ל-30 שניות. אם זה קורה, הקטן את `MAX_RECOMMENDED_ARTISTS` ב-`recommendations.py`.

# Last updated: 2026-04-30

# Last updated: 2026-04-30
