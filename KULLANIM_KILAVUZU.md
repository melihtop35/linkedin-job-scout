# LinkedIn Job Scout — Kodsuz Kullanım Kılavuzu

Bu sistem, bilgisayarınızdaki kişisel tarayıcı profillerine veya dosyalarınıza **hiçbir erişimi olmayan** izole bir Docker sanal tarayıcısı içinde çalışır. LinkedIn iş ilanlarını kriterlerinize göre tarar, uygun olanları LinkedIn hesabınızda **"Kaydet (Save)"** yapar ve yerel rapor dosyanıza işler — **asla iş başvurusu yapmaz.**

---

## Hızlı Başlangıç (3 Adım)

### Adım 1: API Anahtarını `.env` Dosyasına Yapıştırın
1. Proje klasöründeki `.env` dosyasını Not Defteri (Notepad) veya herhangi bir metin düzenleyici ile açın.
2. [ollama.com/settings/keys](https://ollama.com/settings/keys) adresinden aldığınız API anahtarını `OLLAMA_API_KEY=` satırının karşısına yapıştırın ve dosyayı kaydedin.
   - *(Opsiyonel — Mod B)* Bilgisayarınızda yerel Ollama kuruluysa ve `ollama signin` yaptıysanız, Web UI ayarlarından yerel köprü (`http://host.docker.internal:11434`) ile `gemma4:cloud` veya `gemma4:31b-cloud` etiketini de kullanabilirsiniz.
3. İsteğe bağlı olarak `config/candidate_profile.md` dosyasını açıp aradığınız pozisyonları, yetenekleri ve şehir/uzaktan çalışma tercihlerinizi düzenleyin.

### Adım 2: Sistemi Tek Tıkla Başlatın
- **Windows:** `BASLAT.bat` dosyasına çift tıklayın. *(Docker Desktop'ın açık olduğundan emin olun.)*
- **macOS / Linux:** Terminalde `./start.sh` komutunu çalıştırın.

5 saniye içinde tarayıcınızda otomatik olarak iki sekme açılacaktır:
1. **Ajan Kontrol Paneli (Web UI):** `http://localhost:7788`
2. **Canlı Sanal Tarayıcı (noVNC):** `http://localhost:6080`

### Adım 3: LinkedIn'e Giriş Yapın ve Görevi Başlatın
1. **İlk kullanımda:** `http://localhost:6080` (noVNC) sekmesine geçin, `.env` dosyasındaki `VNC_PASSWORD` şifresini (varsayılan: `guvenlisifre123`) girin ve sanal tarayıcıda LinkedIn hesabınıza giriş yapın.
   - Oturum çerezleriniz izole `data/browser_profile/` klasöründe saklanır; sonraki çalıştırmalarda tekrar şifre girmeniz gerekmez.
2. **LLM Ayarları (`http://localhost:7788`):**
   - **Mod A (Önerilen — Doğrudan Ollama Cloud API):** Sağlayıcı olarak **OpenAI** (veya OpenAI-Uyumlu), Base URL olarak `https://ollama.com/v1`, API Key olarak `OLLAMA_API_KEY` değerinizi, Model olarak `gemma4:31b` (veya `gemma4`) seçin.
   - **Mod B (Yerel Ollama Köprüsü):** Sağlayıcı olarak **Ollama**, Base URL olarak `http://host.docker.internal:11434`, Model olarak `gemma4:cloud`, `gemma4:31b-cloud` veya yerel `gemma4:e4b` seçin.
3. **Görevi Başlatın:** `config/AGENT_TASK_PROMPT.md` dosyasının içindeki komut metnini kopyalayıp Web UI'daki **Task / Görev** kutusuna yapıştırın ve **Run / Çalıştır** butonuna basın.

---

## Çıktılar ve Sistemi Durdurma
- **Kaydedilen İlanlar:** Ajanın 75+ uyum puanı verip kaydettiği tüm ilanlar `data/saved_jobs/saved_jobs.md` dosyasına tablo olarak eklenir.
- **Durdurma:** İşiniz bittiğinde **Windows** için `DURDUR.bat` dosyasına çift tıklayın (**macOS/Linux** için `./stop.sh`).
