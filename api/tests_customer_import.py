"""Test import CSV Pelanggan — pengisian negara & provinsi.

Template resmi Olsera TIDAK punya kolom country/province (cuma address,
postal_code, city, subdistrict). Olsera menurunkan Negara & Propinsi dari kotanya
lewat master wilayah; kita belum punya master itu.

Yang bisa dijaga di sini: `negara` jatuh ke 'Indonesia' — default yang sama
dengan form Tambah Pelanggan. Sebelumnya importer tidak mengisinya sama sekali,
jadi pelanggan hasil import ber-negara kosong sementara yang diketik manual
ber-'Indonesia'.
"""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from api.customer_models import Customer

User = get_user_model()

# Persis header template Olsera — tanpa country & province.
HEADER_OLSERA = ('code,customer_type,name,email,phone,address,postal_code,gender,'
                 'dob,expiry_date,is_frozen,city,subdistrict,company,'
                 'accept_newsletter,credit_limit,notes')


def csv_file(header, *baris):
    isi = '\n'.join([header, *baris])
    return SimpleUploadedFile('pelanggan.csv', isi.encode('utf-8'), content_type='text/csv')


class CustomerImportWilayahTest(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner_cust_import', password='password123', role='owner'
        )
        self.client.force_authenticate(user=self.owner)

    def _import(self, berkas):
        return self.client.post('/api/customers/import-csv/', {'file': berkas}, format='multipart')

    def test_negara_default_indonesia_saat_kolomnya_tidak_ada(self):
        res = self._import(csv_file(
            HEADER_OLSERA,
            'CUS0003,,Bella,b@c.com,628123,Jl. Angkasa 17,123456,F,1987-11-01,,1,'
            'Batam,Batu Ampar,,1,0,Catatan',
        ))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        bella = Customer.objects.get(nama='Bella')
        # Sama dengan default form Tambah — bukan string kosong.
        self.assertEqual(bella.negara, 'Indonesia')
        # Kota & kecamatan tetap dari file, apa adanya.
        self.assertEqual(bella.kota, 'Batam')
        self.assertEqual(bella.kecamatan, 'Batu Ampar')

    def test_provinsi_kosong_karena_belum_ada_master_wilayah(self):
        # Menjaga ekspektasi tetap jujur: Olsera mengisi "Kepulauan Riau" dari
        # kota "Batam" lewat master wilayah. Kita belum punya, jadi kosong —
        # BUKAN ditebak. Kalau nanti master wilayahnya ada, test ini yang berubah.
        res = self._import(csv_file(
            HEADER_OLSERA,
            'CUS0003,,Bella,b@c.com,628123,Jl. Angkasa 17,123456,F,1987-11-01,,1,'
            'Batam,Batu Ampar,,1,0,Catatan',
        ))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(Customer.objects.get(nama='Bella').provinsi, '')

    def test_country_dan_province_dipakai_bila_kolomnya_ada(self):
        res = self._import(csv_file(
            HEADER_OLSERA + ',country,province',
            'CUS0009,,Sinta,s@c.com,628999,Jl. Melati 3,40123,F,1990-05-05,,0,'
            'Bandung,Coblong,,0,0,,Malaysia,Selangor',
        ))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        sinta = Customer.objects.get(nama='Sinta')
        # Isi file menang atas default — pelanggan luar negeri tidak dipaksa Indonesia.
        self.assertEqual(sinta.negara, 'Malaysia')
        self.assertEqual(sinta.provinsi, 'Selangor')
