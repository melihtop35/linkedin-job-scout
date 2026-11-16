# AI Job Scout — LinkedIn & Kariyer.net Zincirleme Kullanım Kılavuzu

Bu sistem, **tek bir merkezden (`http://localhost:7788` ve `http://localhost:6080`)** hem **LinkedIn** hem de **Kariyer.net** iş ilanlarını zincirleme olarak tarar.

- **LinkedIn'de uygun bulunan (`>= 75` puan) ilanlar:** LinkedIn hesabınızda **Kaydet** yapılır ve `linkedin-job-scout/data/saved_jobs/saved_jobs.md` dosyasına yazılır.
- **Kariyer.net'te uygun bulunan (`>= 75` puan) ilanlar:** Kariyer.net hesabınızda **Kaydet** yapılır ve `kariyer-job-scout/data/saved_jobs/saved_jobs.md` dosyasına yazılır.
- **Asla iş başvurusu yapmaz.**

---

## Zincir Sıralamasını Nasıl Belirlersiniz?

### Yöntem 1: Web Arayüzünden Tek Tıkla (En Kolay)
`http://localhost:7788` adresindeki **Browser Use Agent** sekmesinde, görev kutusunun hemen üstünde yer alan **"🔗 Otomasyon Zincir Sıralaması"** kutusundan istediğiniz modu seçebilirsiniz:
1. **`LinkedIn ➔ Kariyer.net (Zincir)`** *(Varsayılan — Önce LinkedIn biter, ardından otomatik Kariyer.net başlar)*
2. **`Kariyer.net ➔ LinkedIn (Zincir)`** *(Önce Kariyer.net biter, ardından otomatik LinkedIn başlar)*
3. **`Sadece LinkedIn`**
4. **`Sadece Kariyer.net`**

Hemen yanındaki **"📊 Platform Başına İlan Sayısı"** kaydırıcısından her platformda kaç ilan inceleneceğini (varsayılan: `20` ilan LinkedIn + `20` ilan Kariyer.net = toplam `40` ilan) seçebilirsiniz.

### Yöntem 2: Hangi Klasördeki `BASLAT.bat` Dosyasını Çalıştırdığınıza Göre
- `linkedin-job-scout\BASLAT.bat` çalıştırıldığında varsayılan sıra **`LinkedIn ➔ Kariyer.net`** olarak açılır.
- `kariyer-job-scout\BASLAT.bat` çalıştırıldığında varsayılan sıra **`Kariyer.net ➔ LinkedIn`** olarak açılır.
- Her ikisi de aynı tek portu (`7788` ve `6080`) ve aynı ortak tarayıcı profilini kullanır.

---

## İlk Kurulumda Oturum Açma (`http://localhost:6080`)
1. `http://localhost:6080` (noVNC) sekmesini açın (Şifre: `guvenlisifre123`).
2. Sanal tarayıcıda hem **LinkedIn** hem de **Kariyer.net** hesabınıza bir kez giriş yapın.
3. Çerezler ortak profilde saklandığı için sonraki çalıştırmalarda tekrar giriş yapmanız gerekmez.
