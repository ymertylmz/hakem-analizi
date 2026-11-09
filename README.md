# ⚽ Hakem Analizi

GitHub + Streamlit üzerinde çalışan hakem sezon analizi ilk sürümü.

## Özellikler
- Önceki ve mevcut sezonun **tüm bulunabilen maçları**, sinyal olmasa bile tarih sırasıyla listelenir.
- Hazırlık, kadın, alt yaş ve rezerv kategorileri filtrelenir.
- Lig / ulusal kupa / Şampiyonlar Ligi / Avrupa Ligi / Konferans Ligi **ayrı sıra numaralarına** sahiptir.
- İY skoru, MS skoru ve 1/X/2 cinsinden İY/MS gösterilir.
- Sıra sinyali: aynı kategorinin aynı maç sırasındaki geçen sezon sonucunun 1/2 veya 2/1 olması.
- Hafta sinyali: aynı kategoride ve aynı **sayısal lig turu / maç haftası** karşılığında geçen sezon 1/2 veya 2/1 olması.
- Takım bazlı sarı/kırmızı kartlar için ek API isteklerini kullanıcı kontrollü yükler.
- CSV indirme.

## Kurulum
1. Bu klasördeki dosyaları yeni bir GitHub deposunun köküne yükleyin (app.py, engine.py, requirements.txt ve .streamlit klasörü).
2. [share.streamlit.io](https://share.streamlit.io/) adresinden GitHub deponuzu bağlayın; giriş dosyası `app.py`.
3. Streamlit **App settings > Secrets** alanına şu satırı girin:

   ```toml
   API_FOOTBALL_KEY = "SENIN_API_ANAHTARIN"
   ```

   API anahtarını GitHub'a **asla** eklemeyin. `.streamlit/secrets.toml.example` yalnızca örnektir.
4. Uygulamayı açın. Ülke ve organizasyonları seçin, hakem adını yazın, **Hakemi analiz et** düğmesine basın.
5. Kartları görmek için **Kart verilerini yükle** bölümünü kullanın.

### Bilgisayarda çalışma
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Veri ve doğruluk sınırları
- Sağlayıcı: [API-Football v3](https://www.api-football.com/documentation-v3), `leagues`, `fixtures`, `fixtures/events`.
- API'nin `fixtures` listesi doğrudan hakem adına göre filtrelenemediğinden, **yalnızca seçili lig/kupalar** taranıp maçların `fixture.referee` alanı filtrelenir. Bazı profesyonel müsabakalar seçilmezse hakemin tam kariyer arşivi elde edilmez ve kategori sırası eksik olabilir.
- API'de olmayan hakem veya kart verisi uydurulmaz. Kart olay verisi eksikse 0 yazılmaz, `Veri yok` gösterilir.
- Aynı hafta için yarışmanın API `round` bilgisindeki sayısal **hafta/tur numarası** kullanılır. Avrupa/ulusal kupaların eleme turları için güvenilir haftalık numara olmayan durumda hafta sinyali verilmez.
- Sezon yılı API'de sezon başlangıcı olarak temsil edilir; Brezilya vb. takvim yılı ligleri ayrı ele alınmalıdır.
- Kart olaylarında ikinci sarı kart olayları sağlayıcıya göre farklı biçimlerde raporlanabilir. Bu sürüm yalnızca `Yellow Card` ve `Red Card` detaylarını sayar; resmi disiplin raporunun yerini tutmaz.
- Sayfa önbelleği istek maliyetini azaltır ama çok sayıda organizasyon veya kart olayı yüksek API kredisi tüketebilir.
- Gelecek maçların hakemi henüz atanmadıysa uygulama bunu eşleştiremez.
- Bu ürün örüntü görüntüler; 1/2 ya da 2/1 gerçekleşeceğine ilişkin istatistiksel garanti sunmaz.
