import os
import pandas as pd
import numpy as np

np.random.seed(42)
n = 300

bedrooms = np.random.randint(1, 6, n)
bathrooms = np.random.randint(1, 4, n)
sqft = np.random.randint(500, 4000, n)
age_years = np.random.randint(0, 50, n)
garage = np.random.randint(0, 3, n)
neighborhood = np.random.choice(['A', 'B', 'C', 'D'], n)
school_rating = np.round(np.random.uniform(3, 10, n), 1)

neigh_bonus = np.where(neighborhood == 'A', 30000,
              np.where(neighborhood == 'B', 15000,
              np.where(neighborhood == 'C', 0, -20000)))

price = (
    bedrooms * 15000 +
    bathrooms * 12000 +
    sqft * 120 +
    school_rating * 8000 -
    age_years * 500 +
    garage * 20000 +
    neigh_bonus +
    np.random.normal(0, 15000, n)
).round(0)

df = pd.DataFrame({
    'bedrooms': bedrooms,
    'bathrooms': bathrooms,
    'sqft': sqft,
    'age_years': age_years,
    'garage': garage,
    'neighborhood': neighborhood,
    'school_rating': school_rating,
    'price': price,
})

out = os.path.join(os.path.dirname(__file__), 'uploads', 'house_price_demo.csv')
os.makedirs(os.path.dirname(out), exist_ok=True)
df.to_csv(out, index=False)

print(f"Created: {out}")
print(f"Shape: {df.shape}")
print(df.head(3).to_string())
print(f"\nTarget: min={df['price'].min():.0f}  max={df['price'].max():.0f}  mean={df['price'].mean():.0f}")
