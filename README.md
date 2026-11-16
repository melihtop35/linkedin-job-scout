# LinkedIn Job Scout

LinkedIn Job Scout, LinkedIn ve Kariyer.net üzerinde iş ilanlarını otomatik olarak tarayan, uygun ilanları puanlayarak kaydeden ve merkezi bir web arayüzü üzerinden yöneten bir Python tabanlı otomasyon projesidir.

Proje, tek bir kontrol paneli üzerinden iş akışını yönetir; aynı tarayıcı profili kullanılarak oturumlar korunur ve otomasyon zinciri (LinkedIn → Kariyer.net veya tam tersi) yapılandırılabilir.

## Özellikler

- LinkedIn ve Kariyer.net üzerinde zincirleme ilan tarama
- Her platform için ilan sayısı ve sıralama ayarı
- İlan değerlendirme ve skor tabanlı filtreleme
- Uygun ilanları platformlarda "Kaydet" işlemiyle işaretleme
- Tek merkezli web arayüzü (`http://localhost:7788`)
- Canlı tarayıcı izleme (`http://localhost:6080` / noVNC)
- Docker tabanlı çalıştırma
- Uzun süreli oturum koruması için tarayıcı profilinin saklanması
- Ollama / OpenAI uyumlu LLM desteği

## Çalışma Prensibi

- Varsayılan zincir sırası: `LinkedIn → Kariyer.net`
- Alternatif sıra: `Kariyer.net → LinkedIn`
- Otomasyon, `>= 75` puan alan uygun ilanları belirler ve kaydeder.
- İş başvurusu yapmaz; sadece uygun ilanları işaretler ve kaydeder.

## Gereksinimler

- Docker Desktop veya Docker Engine
- Linux/macOS/WSL2 bazlı ortam (Windows için Docker Desktop önerilir)
- Ollama çalışıyor olmalıdır (varsayılan yerel mod)
- `../kariyer-job-scout` klasörünün aynı üst dizinde bulunması önerilir; proje docker-compose içerisinde bu klasöre referans verir.

## Hızlı Başlangıç

1. Proje kök dizininde `.env.example` dosyasını kopyalayın:

   ```bash
   cp .env.example .env
   ```

2. İsterseniz `.env` dosyasında aşağıları güncelleyin:

   ```dotenv
   CHAIN_ORDER=linkedin,kariyer
   JOBS_PER_PLATFORM=20
   DEFAULT_LLM_MODEL=gemma4:12b
   OLLAMA_ENDPOINT=http://host.docker.internal:11434
   VNC_PASSWORD=guvenlisifre123
   ```

3. Projeyi başlatın:

   ```bash
   ./start.sh
   ```

4. Tarayıcı arayüzünü açın:

   - Kontrol paneli: `http://localhost:7788`
   - noVNC: `http://localhost:6080`

5. İlk kurulumda noVNC üzerinden LinkedIn ve Kariyer.net hesabınıza giriş yapın.

6. Ardından otomasyon zincirini web arayüzünden seçerek taramayı başlatın.

## Durdurma

```bash
./stop.sh
```

## Varsayılan Kullanıcı Bilgileri

- noVNC şifresi: `guvenlisifre123`
- Varsayılan zincir: `linkedin,kariyer`
- Varsayılan ilan sayısı: `20` ilan / platform

## Proje Yapısı

```text
.
├── .env.example
├── .gitignore
├── BASLAT.bat
├── DURDUR.bat
├── KULLANIM_KILAVUZU.md
├── docker-compose.yml
├── start.sh
├── stop.sh
├── config/
│   └── AGENT_TASK_PROMPT.md
├── src/
│   ├── agent/
│   │   └── browser_use/
│   │       └── browser_use_agent.py
│   ├── controller/
│   │   └── custom_controller.py
│   ├── utils/
│   │   ├── config.py
│   │   └── llm_provider.py
│   └── webui/
│       └── components/
│           ├── agent_settings_tab.py
│           ├── browser_settings_tab.py
│           └── browser_use_agent_tab.py
└── data/
    └── browser_profile/
```

## Ana Dosyalar

- `docker-compose.yml`: Docker servislerini ve port/volume ayarlarını tanımlar.
- `start.sh`: Ortam kontrolünü yapar ve konteyneri başlatır.
- `stop.sh`: Konteyneri güvenli şekilde durdurur.
- `src/agent/browser_use/browser_use_agent.py`: Tarayıcı otomasyonunu çalıştırır.
- `src/controller/custom_controller.py`: İş akışı kontrolünü sağlar.
- `src/webui/components/*`: Web arayüzü bileşenlerini içerir.
- `config/AGENT_TASK_PROMPT.md`: Ajan için çalışma talimatını içerir.

## Notlar

- Bu proje, iş ilanlarını tararken kullanılan tarayıcı profili ve oturum bilgilerini yerelde korur.
- İlan kayıtları ve tarayıcı verileri `data/` klasörü altında tutulur.
- LLM bağlantısı için yerel Ollama veya doğrudan OpenAI uyumlu uç nokta kullanılabilir.
- Proje, platforma göre kullanıcı hesabı ile çalışır ve hesabınıza giriş yapmanız gerekir.

## Lisans

Bu repository için özel lisans bilgisi görünmemektedir. Kendi kullanımınız için gerekli hakları kontrol etmeyi unutmayın.

## İletişim / Kullanım Desteği

Detaylı kullanım notları için `KULLANIM_KILAVUZU.md` dosyasına bakabilirsiniz.

---

Bu README, projeyi daha kolay kurup çalıştırmak için temel kullanım bilgilerini kapsar. Daha ileri düzey bir sürüm isterseniz, İngilizce versiyon, ekran görüntüsü bölümü veya kurulum akışı eklenebilir.
